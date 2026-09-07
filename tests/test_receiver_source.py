"""Tests for resolving a bare receiver address to a working aircraft.json URL.

This exists because typing the obvious thing -- the receiver's IP -- used to be routed to
the FILE loader and fail with "file not found: 192.168.3.80", a message that never even
suggests the network was skipped. The dispatch table below is the regression guard for
that, and the probe tests cover what happens once a host is recognised.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from adsbtui.sources import (
    SourceUnreachable,
    looks_like_bare_host,
    make_source,
)
from adsbtui.sources.http import HttpSource
from adsbtui.sources.jsonfile import FileSource
from adsbtui.sources.receiver import ReceiverSource, candidate_urls

PAYLOAD = b'{"now": 1.0, "messages": 2, "aircraft": [{"hex": "abc123"}]}'


class TestDispatch:
    @pytest.mark.parametrize(
        "text",
        ["192.168.3.80", "192.168.3.80:8080", "adsb.local", "adsb-pi", "10.0.0.5"],
    )
    def test_a_bare_address_becomes_a_receiver_probe(self, text):
        assert isinstance(make_source(text), ReceiverSource)

    @pytest.mark.parametrize("text", ["http://192.168.3.80", "http://192.168.3.80/"])
    def test_a_scheme_with_no_path_is_still_a_receiver(self, text):
        # "http://host" names a machine, not a document.
        source = make_source(text)
        assert isinstance(source, ReceiverSource)
        assert source.host == "192.168.3.80"

    def test_a_full_url_is_used_verbatim(self):
        url = "http://host/tar1090/data/aircraft.json"
        source = make_source(url)
        assert isinstance(source, HttpSource)
        assert source.url == url

    @pytest.mark.parametrize(
        "text",
        [
            "/run/readsb/aircraft.json",
            "file:///run/readsb/aircraft.json",
            "./feed.json",
            "aircraft.json",
            "tests/fixtures/aircraft.json",
            "C:\\data\\aircraft.json",
        ],
    )
    def test_paths_are_still_files(self, text):
        assert isinstance(make_source(text), FileSource)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("192.168.3.80", True),
            ("host:8080", True),
            ("aircraft.json", False),  # a filename, not a host
            ("data.csv", False),
            ("./x", False),
            ("/run/x", False),
            ("", False),
        ],
    )
    def test_bare_host_detection(self, text, expected):
        assert looks_like_bare_host(text) is expected


class TestCandidates:
    def test_covers_the_paths_receiver_images_publish(self):
        urls = candidate_urls("1.2.3.4")
        assert "http://1.2.3.4/tar1090/data/aircraft.json" in urls
        assert "http://1.2.3.4/skyaware/data/aircraft.json" in urls
        assert "http://1.2.3.4/dump1090-fa/data/aircraft.json" in urls
        assert "http://1.2.3.4:8080/data/aircraft.json" in urls

    def test_an_explicit_port_is_taken_at_its_word(self):
        # Someone who typed a port meant that port; adding :8080 to it would be nonsense.
        urls = candidate_urls("1.2.3.4:8080")
        assert all(":8080/" in url for url in urls)
        assert not any(":8080:8080" in url for url in urls)


class _Handler(http.server.BaseHTTPRequestHandler):
    """Serves aircraft.json at ONE path and 404s everywhere else, like a real image."""

    served_path = "/skyaware/data/aircraft.json"

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's required name
        if self.path == self.served_path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(PAYLOAD)))
            self.end_headers()
            self.wfile.write(PAYLOAD)
        else:
            self.send_error(404)

    def log_message(self, *args):  # keep the test output clean
        pass


@pytest.fixture
def receiver_server():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


class TestProbing:
    def test_finds_the_one_path_that_answers(self, receiver_server):
        source = ReceiverSource(receiver_server)
        data = source.fetch(timeout=2.0)
        assert data["aircraft"][0]["hex"] == "abc123"
        assert source.resolved_url.endswith("/skyaware/data/aircraft.json")

    def test_the_resolved_url_is_reused_rather_than_reprobed(self, receiver_server):
        source = ReceiverSource(receiver_server)
        source.fetch(timeout=2.0)
        resolved = source.resolved_url
        source.fetch(timeout=2.0)
        assert source.resolved_url == resolved

    def test_a_host_serving_nothing_says_what_it_tried(self):
        # The old failure mode was a message that named a file. This one has to name URLs,
        # or the user has no idea the network was even attempted.
        source = ReceiverSource("127.0.0.1:9")  # discard port: nothing listens
        with pytest.raises(SourceUnreachable) as excinfo:
            source.fetch(timeout=0.5)
        message = str(excinfo.value)
        assert "127.0.0.1:9" in message
        assert "http://" in message
        assert "tar1090" in message
