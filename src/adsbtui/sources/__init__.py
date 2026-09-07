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


def looks_like_bare_host(text: str) -> bool:
    """True if text is a hostname or IP with no scheme and no path, e.g. "192.168.3.80".

    This is the address a person types when asked where their receiver is, and it is
    neither a URL nor a file path. Anything containing a path separator, a leading dot, or
    a filename-looking suffix is not one: "/run/readsb/aircraft.json" and "./feed.json"
    must still be files.
    """
    text = text.strip()
    if not text or "/" in text or "\\" in text:
        return False
    if text.startswith("."):
        return False

    host = text.rsplit(":", 1)[0] if text.count(":") == 1 else text
    if not host:
        return False
    # A trailing ".json"/".txt" means someone meant a file in the current directory.
    if "." in host and host.rsplit(".", 1)[-1].isalpha() and len(host.rsplit(".", 1)[-1]) > 1:
        suffix = host.rsplit(".", 1)[-1].lower()
        if suffix in {"json", "txt", "csv", "gz", "log"}:
            return False
    return all(c.isalnum() or c in ".-:[]" for c in text)


def make_source(url_or_path: str, max_bytes: int = DEFAULT_MAX_BYTES) -> Source:
    """Build the right Source for a URL, a bare receiver address, or a filesystem path.

    "http://..." and "https://..." return an HttpSource, except for a URL with no path of
    its own ("http://192.168.3.80"), which is a receiver address wearing a scheme and gets
    probed like one. A bare host or host:port ("192.168.3.80", "adsb.local:8080") returns
    a ReceiverSource, which finds the right aircraft.json path on that machine itself.
    Anything else -- "/run/readsb/aircraft.json", a relative path, or a "file://" URL --
    returns a FileSource.
    """
    from adsbtui.sources.http import HttpSource
    from adsbtui.sources.jsonfile import FileSource
    from adsbtui.sources.receiver import ReceiverSource

    text = url_or_path.strip()

    if text.startswith(("http://", "https://")):
        without_scheme = text.split("://", 1)[1]
        # "http://host" and "http://host/" name a machine, not a document: probe them.
        if "/" not in without_scheme.rstrip("/"):
            return ReceiverSource(without_scheme.rstrip("/"), max_bytes)
        return HttpSource(text, max_bytes)

    if looks_like_bare_host(text):
        return ReceiverSource(text, max_bytes)

    path = text
    if path.startswith("file://"):
        path = path[len("file://") :]

    return FileSource(path, max_bytes)
