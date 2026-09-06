"""HTTP(S) Source: fetch aircraft.json from a dump1090/readsb web server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from adsbtui.sources import SourceInvalidData, SourceTimeout, SourceUnreachable


class HttpSource:
    """Fetch aircraft.json over HTTP or HTTPS."""

    def __init__(self, url: str, max_bytes: int) -> None:
        self.url = url
        self.max_bytes = max_bytes

    def fetch(self, timeout: float) -> dict:
        request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError:
                        declared_size = None
                    if declared_size is not None and declared_size > self.max_bytes:
                        raise SourceInvalidData(
                            f"response declared {declared_size} bytes, "
                            f"exceeding max_bytes={self.max_bytes}"
                        )
                body = response.read(self.max_bytes + 1)
        except SourceInvalidData:
            raise
        except TimeoutError as exc:
            raise SourceTimeout(f"timed out fetching {self.url}: {exc}") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise SourceTimeout(f"timed out fetching {self.url}: {exc}") from exc
            raise SourceUnreachable(f"could not reach {self.url}: {exc}") from exc
        except OSError as exc:
            raise SourceUnreachable(f"could not reach {self.url}: {exc}") from exc

        if len(body) > self.max_bytes:
            raise SourceInvalidData(
                f"response body exceeded max_bytes={self.max_bytes} while reading"
            )

        try:
            data = json.loads(body)
        except json.JSONDecodeError as exc:
            raise SourceInvalidData(f"response from {self.url} was not valid JSON: {exc}") from exc

        if not isinstance(data, dict) or not isinstance(data.get("aircraft"), list):
            raise SourceInvalidData(
                f"response from {self.url} was not an aircraft.json object with an 'aircraft' list"
            )

        return data
