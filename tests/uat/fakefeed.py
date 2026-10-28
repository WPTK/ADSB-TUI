"""A scriptable aircraft.json server for UAT.

The server answers every request from a queue of steps; the last step repeats once the queue
is down to one. A test builds a timeline of faults and recoveries without sleeping:

    feed.script(ok(fleet()), http_error(500), stall(30), ok(fleet()))

`feed.requests` counts how many fetches arrived, so a test can wait for "the app has fetched
N times" instead of guessing at time.
"""

from __future__ import annotations

import gzip
import http.server
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "aircraft.json"

HOME_LAT, HOME_LON = 37.7749, -122.4194


def ac(hex_: str, lat: float, lon: float, **fields) -> dict:
    """One aircraft record with sensible defaults; override anything via keywords."""
    rec = {
        "hex": hex_,
        "type": "adsb_icao",
        "flight": hex_.upper(),
        "alt_baro": 10000,
        "gs": 250.0,
        "track": 90.0,
        "lat": lat,
        "lon": lon,
        "seen": 0.2,
        "seen_pos": 0.2,
        "squawk": "1200",
        "messages": 100,
    }
    rec.update(fields)
    return rec


def fixture_aircraft() -> list[dict]:
    return json.loads(FIXTURE.read_text("utf-8"))["aircraft"]


@dataclass
class Step:
    kind: str
    payload: object = None


def ok(aircraft: list[dict] | Callable[[], list[dict]]) -> Step:
    return Step("ok", aircraft)


def frozen(aircraft: list[dict]) -> Step:
    """A healthy-looking response whose `now` never advances (a dead decoder)."""
    return Step("frozen", aircraft)


def http_error(code: int) -> Step:
    return Step("http", code)


def stall(seconds: float) -> Step:
    return Step("stall", seconds)


def drip(seconds: float = 60.0) -> Step:
    """Promise a big body, then send one byte every 0.2 s: defeats a per-read timeout."""
    return Step("drip", seconds)


def bad_json() -> Step:
    return Step("bad_json")


def huge(nbytes: int) -> Step:
    return Step("huge", nbytes)


def raw(body: bytes) -> Step:
    return Step("raw", body)


class FakeFeed:
    def __init__(self) -> None:
        self.requests = 0
        self._steps: list[Step] = [ok(fixture_aircraft)]
        self._lock = threading.Lock()
        self._frozen_now = 1_700_000_000.0
        self._closed = threading.Event()
        feed = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                feed._respond(self)

            def log_message(self, format, *args) -> None:  # noqa: A002
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/data/aircraft.json"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    # ------------------------------------------------------------------ scripting

    def script(self, *steps: Step) -> None:
        with self._lock:
            self._steps = list(steps) or [ok(fixture_aircraft)]

    def _next(self) -> Step:
        with self._lock:
            self.requests += 1
            if len(self._steps) > 1:
                return self._steps.pop(0)
            return self._steps[0]

    # ------------------------------------------------------------------ serving

    def _respond(self, h: http.server.BaseHTTPRequestHandler) -> None:
        step = self._next()
        try:
            if step.kind in ("ok", "frozen"):
                items = step.payload() if callable(step.payload) else step.payload
                now = self._frozen_now if step.kind == "frozen" else time.time()
                body = json.dumps({"now": now, "messages": 1, "aircraft": items}).encode()
                self._send(h, 200, body)
            elif step.kind == "http":
                self._send(h, int(step.payload), b"{}")
            elif step.kind == "bad_json":
                self._send(h, 200, b'{"aircraft": [{"hex": ')
            elif step.kind == "raw":
                self._send(h, 200, step.payload)
            elif step.kind == "huge":
                filler = b" " * int(step.payload)
                self._send(h, 200, b'{"aircraft": []}' + filler)
            elif step.kind == "stall":
                self._closed.wait(float(step.payload))
            elif step.kind == "drip":
                h.send_response(200)
                h.send_header("Content-Length", "100000000")
                h.end_headers()
                h.wfile.write(b"{")
                h.wfile.flush()
                end = time.monotonic() + float(step.payload)
                while time.monotonic() < end and not self._closed.is_set():
                    time.sleep(0.2)
                    h.wfile.write(b" ")
                    h.wfile.flush()
        except OSError:
            pass  # the client gave up, which is often the point

    @staticmethod
    def _send(h: http.server.BaseHTTPRequestHandler, code: int, body: bytes) -> None:
        h.send_response(code)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def wait_requests(self, n: int, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while self.requests < n:
            if time.monotonic() > deadline:
                raise AssertionError(f"only {self.requests} requests, wanted {n}")
            time.sleep(0.02)

    def close(self) -> None:
        self._closed.set()
        self._server.shutdown()
        self._server.server_close()


def serve_registry(delay_s: float = 0.0) -> http.server.HTTPServer:
    """A local tar1090-db: two aircraft, gzipped, served over HTTP (optionally slowly)."""
    body = gzip.compress(
        b"a1b2c3;N123UA;B738;00;BOEING 737-800;2010;UNITED AIR LINES;\n"
        b"abc123;N456XY;C172;00;CESSNA 172;1980;SOME OWNER;\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            time.sleep(delay_s)
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args) -> None:  # noqa: A002
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
