"""Local file Source: read aircraft.json straight off disk (e.g. /run/readsb/aircraft.json)."""

from __future__ import annotations

from adsbtui.sources import SourceInvalidData, SourceUnreachable, parse_payload


class FileSource:
    """Fetch aircraft.json from a local file path."""

    def __init__(self, path: str, max_bytes: int) -> None:
        self.path = path
        self.max_bytes = max_bytes

    def fetch(self, timeout: float) -> dict:
        # timeout is accepted for interface uniformity with HttpSource but is not
        # enforceable on a local filesystem read.
        del timeout

        try:
            with open(self.path, "rb") as f:
                body = f.read(self.max_bytes + 1)
        except FileNotFoundError as exc:
            raise SourceUnreachable(f"file not found: {self.path}") from exc
        except PermissionError as exc:
            raise SourceUnreachable(f"permission denied: {self.path}") from exc
        except OSError as exc:
            raise SourceUnreachable(f"could not read {self.path}: {exc}") from exc

        if len(body) > self.max_bytes:
            raise SourceInvalidData(f"{self.path} exceeds max_bytes={self.max_bytes}")

        return parse_payload(body, self.path)
