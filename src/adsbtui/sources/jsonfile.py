"""Local file Source: read aircraft.json straight off disk (e.g. /run/readsb/aircraft.json)."""

from __future__ import annotations

import json
import os

from adsbtui.sources import SourceInvalidData, SourceUnreachable


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
            size = os.path.getsize(self.path)
        except FileNotFoundError as exc:
            raise SourceUnreachable(f"file not found: {self.path}") from exc
        except PermissionError as exc:
            raise SourceUnreachable(f"permission denied: {self.path}") from exc
        except OSError as exc:
            raise SourceUnreachable(f"could not stat {self.path}: {exc}") from exc

        if size > self.max_bytes:
            raise SourceInvalidData(
                f"{self.path} is {size} bytes, exceeding max_bytes={self.max_bytes}"
            )

        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as exc:
            raise SourceUnreachable(f"file not found: {self.path}") from exc
        except PermissionError as exc:
            raise SourceUnreachable(f"permission denied: {self.path}") from exc
        except json.JSONDecodeError as exc:
            raise SourceInvalidData(f"{self.path} was not valid JSON: {exc}") from exc

        if not isinstance(data, dict) or not isinstance(data.get("aircraft"), list):
            raise SourceInvalidData(
                f"{self.path} was not an aircraft.json object with an 'aircraft' list"
            )

        return data
