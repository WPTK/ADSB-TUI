"""Tests for adsbtui.sources: FileSource, HttpSource, and the make_source() factory."""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path

import pytest

from adsbtui.sources import (
    SourceInvalidData,
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

    def test_connection_refused_is_unreachable(self):
        # Nothing is listening on this port, so the connection should fail immediately.
        source = HttpSource("http://127.0.0.1:1/aircraft.json", max_bytes=8_000_000)
        with pytest.raises(SourceUnreachable):
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
