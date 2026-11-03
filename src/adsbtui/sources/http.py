"""HTTP(S) Source: fetch aircraft.json from a dump1090/readsb web server."""

from __future__ import annotations

import contextlib
import http.client
import time
import urllib.error
import urllib.request

from adsbtui.sources import SourceInvalidData, SourceTimeout, SourceUnreachable, parse_payload

_CHUNK = 65536


class HttpSource:
    """Fetch aircraft.json over HTTP or HTTPS."""

    def __init__(self, url: str, max_bytes: int) -> None:
        self.url = url
        self.max_bytes = max_bytes

    def fetch(self, timeout: float) -> dict:
        """Fetch and parse one aircraft.json, giving up `timeout` seconds after starting.

        The timeout is a wall-clock budget for reading the body, not a per-read limit: a
        server that drips one byte every few seconds never trips a per-read timeout and
        would otherwise hold the caller forever.
        """
        deadline = time.monotonic() + timeout
        try:
            request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        except ValueError as exc:
            raise SourceUnreachable(f"not a usable URL: {self.url}: {exc}") from exc

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                self._check_declared_size(response)
                body = self._read_body(response, deadline)
        except (SourceInvalidData, SourceTimeout):
            raise
        except TimeoutError as exc:
            raise SourceTimeout(f"timed out fetching {self.url}: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise SourceTimeout(f"timed out fetching {self.url}: {exc}") from exc
            raise SourceUnreachable(f"could not reach {self.url}: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise SourceUnreachable(f"could not reach {self.url}: {exc}") from exc

        return parse_payload(body, f"response from {self.url}")

    def _check_declared_size(self, response: http.client.HTTPResponse) -> None:
        content_length = response.headers.get("Content-Length")
        if content_length is None:
            return
        try:
            declared_size = int(content_length)
        except ValueError:
            return
        if declared_size > self.max_bytes:
            raise SourceInvalidData(
                f"response declared {declared_size} bytes, exceeding max_bytes={self.max_bytes}"
            )

    def _read_body(self, response: http.client.HTTPResponse, deadline: float) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceTimeout(f"timed out fetching {self.url}: body not complete in time")
            _limit_next_read(response, remaining)
            # read1 returns after one underlying read; read(n) would wait for all n bytes
            # and never get a chance to look at the clock.
            chunk = response.read1(_CHUNK)
            if not chunk:
                return b"".join(chunks)
            total += len(chunk)
            if total > self.max_bytes:
                raise SourceInvalidData(
                    f"response body exceeded max_bytes={self.max_bytes} while reading"
                )
            chunks.append(chunk)


def _limit_next_read(response: http.client.HTTPResponse, seconds: float) -> None:
    """Shrink the socket timeout so one blocked read cannot outlast the deadline.

    http.client exposes no public handle on the socket, so this reaches for it and does
    nothing if the layout differs; the deadline is still checked between reads.
    """
    sock = getattr(getattr(response.fp, "raw", None), "_sock", None)
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.settimeout(max(seconds, 0.001))
