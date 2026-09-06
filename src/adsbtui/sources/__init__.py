"""Sources of aircraft.json data.

A Source is anything with a fetch(timeout) -> dict method that returns the parsed,
top-level aircraft.json object (a dict with "now"/"messages"/"aircraft" keys), unparsed.
Turning that raw dict into a Snapshot/Aircraft is normalize.py's job, not this package's.

This module defines the shared exception hierarchy, the Source protocol, and a factory
function that picks the right concrete Source (HTTP or local file) from a plain string.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

DEFAULT_MAX_BYTES = 8_000_000


class SourceError(Exception):
    """Base class for all errors raised by a Source's fetch()."""


class SourceUnreachable(SourceError):
    """The source could not be reached at all: connection refused, DNS failure,
    file not found, permission denied, etc."""


class SourceTimeout(SourceError):
    """The fetch did not complete within the requested timeout."""


class SourceInvalidData(SourceError):
    """A response was received but could not be used: not valid JSON, too large,
    or not a dict with an "aircraft" list."""


@runtime_checkable
class Source(Protocol):
    """Anything that can fetch a raw aircraft.json payload."""

    def fetch(self, timeout: float) -> dict:
        """Return the parsed top-level aircraft.json object.

        Raises SourceUnreachable, SourceTimeout, or SourceInvalidData on failure.
        """
        ...


def make_source(url_or_path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> Source:
    """Build the right Source for a URL or filesystem path.

    "http://..." and "https://..." strings return an HttpSource. Anything else -- a bare
    path like "/run/readsb/aircraft.json", a relative path, or a "file://" URL (whose
    prefix is stripped) -- returns a FileSource.
    """
    from adsbtui.sources.http import HttpSource
    from adsbtui.sources.jsonfile import FileSource

    if url_or_path.startswith(("http://", "https://")):
        return HttpSource(url_or_path, max_bytes)

    path = url_or_path
    if path.startswith("file://"):
        path = path[len("file://") :]

    return FileSource(path, max_bytes)
