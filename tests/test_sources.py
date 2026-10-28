"""Tests for adsbtui.sources: FileSource, HttpSource, and the make_source() factory."""

from __future__ import annotations

import http.server
import json
import threading
import time
from pathlib import Path

import pytest

from adsbtui.sources import (
    SourceError,
    SourceInvalidData,
    SourceTimeout,
    SourceUnreachable,
    make_source,
)
from adsbtui.sources.http import HttpSource
from adsbtui.sources.jsonfile import FileSource

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aircraft.json"
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()
EXPECTED_AIRCRAFT_COUNT = len(json.loads(FIXTURE_BYTES)["aircraft"])


class TestFileSource:
    def test_fetch_returns_parsed_dict(self):
        source = FileSource(str(FIXTURE_PATH), max_bytes=8_000_000)
        data = source.fetch(timeout=1.0)
        assert isinstance(data, dict)
        assert isinstance(data["aircraft"], list)
        assert len(data["aircraft"]) == EXPECTED_AIRCRAFT_COUNT

    def test_missing_file_is_unreachable(self):
        source = FileSource("/nonexistent/path/does-not-exist.json", max_bytes=8_000_000)
        with pytest.raises(SourceUnreachable):
            source.fetch(timeout=1.0)

    def test_max_bytes_rejected(self):
        source = FileSource(str(FIXTURE_PATH), max_bytes=10)
        with pytest.raises(SourceInvalidData):
            source.fetch(timeout=1.0)

    def test_bad_json_is_invalid_data(self, tmp_path):
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json")
        source = FileSource(str(bad_file), max_bytes=8_000_000)
        with pytest.raises(SourceInvalidData):
            source.fetch(timeout=1.0)

    def test_wrong_shape_is_invalid_data(self, tmp_path):
        no_aircraft = tmp_path / "no_aircraft.json"
        no_aircraft.write_text(json.dumps({"now": 1.0, "messages": 1}))
        source = FileSource(str(no_aircraft), max_bytes=8_000_000)
        with pytest.raises(SourceInvalidData):
            source.fetch(timeout=1.0)


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    """Serves the aircraft.json fixture bytes as application/json for every request."""

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's naming convention
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(FIXTURE_BYTES)))
        self.end_headers()
        self.wfile.write(FIXTURE_BYTES)

    def log_message(self, format, *args):  # noqa: A002 -- matches base class signature
        pass  # keep test output quiet


class TestHttpSource:
    def _start_server(self) -> tuple[http.server.ThreadingHTTPServer, int]:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, port

    def test_fetch_returns_parsed_dict(self):
        server, port = self._start_server()
        try:
            source = HttpSource(f"http://127.0.0.1:{port}/aircraft.json", max_bytes=8_000_000)
            data = source.fetch(timeout=5.0)
            assert isinstance(data, dict)
            assert isinstance(data["aircraft"], list)
            assert len(data["aircraft"]) == EXPECTED_AIRCRAFT_COUNT
        finally:
            server.shutdown()
            server.server_close()

    def test_max_bytes_rejected(self):
        server, port = self._start_server()
        try:
            source = HttpSource(f"http://127.0.0.1:{port}/aircraft.json", max_bytes=10)
            with pytest.raises(SourceInvalidData):
                source.fetch(timeout=5.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_unreachable_endpoint_raises_a_typed_source_error(self):
        """Nothing is listening on this port, so the fetch must fail as one of our own
        exception types rather than leaking a raw urllib error to the caller.

        Deliberately asserts the SourceError base class rather than a specific subclass:
        how a host reports "nothing here" is platform-dependent. Linux answers a connection
        to a dead port with an immediate RST, which surfaces as SourceUnreachable; Windows
        silently drops the packet instead, so the same call surfaces as SourceTimeout after
        the timeout expires. Both are correct classifications of what actually happened,
        and pinning either one makes the test a statement about the host's TCP stack
        instead of about this module.
        """
        source = HttpSource("http://127.0.0.1:1/aircraft.json", max_bytes=8_000_000)
        with pytest.raises(SourceError):
            source.fetch(timeout=2.0)


class TestMakeSource:
    def test_http_url_picks_http_source(self):
        source = make_source("http://example.com/aircraft.json")
        assert isinstance(source, HttpSource)

    def test_https_url_picks_http_source(self):
        source = make_source("https://example.com/aircraft.json")
        assert isinstance(source, HttpSource)

    def test_bare_path_picks_file_source(self):
        source = make_source("/tmp/x.json")
        assert isinstance(source, FileSource)
        assert source.path == "/tmp/x.json"

    def test_file_url_strips_prefix_and_picks_file_source(self):
        source = make_source("file:///tmp/x.json")
        assert isinstance(source, FileSource)
        assert source.path == "/tmp/x.json"

    def test_max_bytes_is_passed_through(self):
        source = make_source("/tmp/x.json", max_bytes=123)
        assert source.max_bytes == 123


# Hostile bodies: whatever a server sends, fetch() raises a SourceError and nothing else.

NOT_UTF8 = b'{"aircraft": [], "x": "\xff\xfe\xfa"}'
DEEP = b"[" * 200_000 + b"]" * 200_000


class TestFileSourceHostileInput:
    @pytest.mark.parametrize(
        "body",
        [NOT_UTF8, DEEP, b"", b"\x00\x00", b"[1, 2]", b"nan"],
        ids=["not-utf8", "deep", "empty", "nul", "array", "nan"],
    )
    def test_unusable_bodies_are_invalid_data(self, tmp_path, body):
        path = tmp_path / "aircraft.json"
        path.write_bytes(body)
        with pytest.raises(SourceInvalidData):
            FileSource(str(path), max_bytes=8_000_000).fetch(timeout=1.0)

    def test_a_directory_is_unreachable_not_a_traceback(self, tmp_path):
        with pytest.raises(SourceUnreachable):
            FileSource(str(tmp_path), max_bytes=8_000_000).fetch(timeout=1.0)

    def test_oversize_is_rejected_by_the_bytes_actually_read(self, tmp_path):
        path = tmp_path / "aircraft.json"
        path.write_text('{"aircraft": [' + "1," * 100 + "1]}")
        with pytest.raises(SourceInvalidData, match="max_bytes"):
            FileSource(str(path), max_bytes=50).fetch(timeout=1.0)


def _serve(handler_cls):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _body_handler(body: bytes):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Handler


class DripHandler(http.server.BaseHTTPRequestHandler):
    """Promises a big body, then sends one byte every 0.2 s, never idle long enough to trip
    a per-read timeout."""

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "100000")
        self.end_headers()
        try:
            self.wfile.write(b"{")
            self.wfile.flush()
            for _ in range(200):
                time.sleep(0.2)
                self.wfile.write(b" ")
                self.wfile.flush()
        except OSError:
            pass

    def log_message(self, format, *args):  # noqa: A002
        pass


class TestHttpSourceHostileInput:
    @pytest.mark.parametrize("body", [NOT_UTF8, DEEP, b"[]"], ids=["not-utf8", "deep", "array"])
    def test_unusable_bodies_are_invalid_data(self, body):
        server, port = _serve(_body_handler(body))
        try:
            with pytest.raises(SourceInvalidData):
                HttpSource(f"http://127.0.0.1:{port}/", max_bytes=8_000_000).fetch(timeout=5.0)
        finally:
            server.shutdown()
            server.server_close()

    def test_a_drip_feed_times_out_on_the_wall_clock(self):
        server, port = _serve(DripHandler)
        try:
            started = time.monotonic()
            with pytest.raises(SourceTimeout):
                HttpSource(f"http://127.0.0.1:{port}/", max_bytes=8_000_000).fetch(timeout=1.0)
            assert time.monotonic() - started < 2.0
        finally:
            server.shutdown()
            server.server_close()

    def test_an_unusable_url_is_unreachable(self):
        with pytest.raises(SourceUnreachable):
            HttpSource("http://", max_bytes=1000).fetch(timeout=1.0)
