"""A Source built from a bare receiver address, e.g. "192.168.3.80".

Typing the receiver's address is the obvious thing to do, and it used to fail in the most
confusing way available: a string with no scheme fell through to the file loader, so
`--url 192.168.3.80` reported "file not found: 192.168.3.80". Nothing about that message
tells you the tool never even tried the network.

So a bare host now means "find aircraft.json on this machine for me". Receiver images
publish it at a handful of well-known paths, and which one depends on whether the image
is tar1090, SkyAware, dump1090-fa's own alias, or readsb's built-in server on :8080.
ReceiverSource tries them in that order on the first fetch, keeps whichever answered, and
uses it for every fetch afterwards, so the probe cost is paid once per run rather than
every poll.
"""

from __future__ import annotations

import logging

from adsbtui.sources import DEFAULT_MAX_BYTES, SourceError, SourceUnreachable

log = logging.getLogger("adsbtui")

#: Where receiver images actually publish aircraft.json, in the order worth trying. The
#: setup wizard probes this same list, so what the wizard finds and what a typed-in
#: address resolves to can never disagree.
CANDIDATE_PATHS: tuple[str, ...] = (
    "/tar1090/data/aircraft.json",
    "/skyaware/data/aircraft.json",
    "/dump1090-fa/data/aircraft.json",
    "/data/aircraft.json",
)

#: readsb and dump1090 also serve their own copy on :8080 when no front-end web server is
#: installed, which is common on a bare readsb install.
CANDIDATE_PORTS: tuple[int | None, ...] = (None, 8080)


def candidate_urls(host: str) -> list[str]:
    """Every aircraft.json URL worth trying for a bare host or host:port.

    A host that already carries an explicit port is taken at its word and only the paths
    are varied; without one, port 80 is tried first and then :8080.
    """
    host = host.strip().rstrip("/")
    has_port = ":" in host.rsplit("]", 1)[-1]

    urls: list[str] = []
    for port in CANDIDATE_PORTS:
        if has_port and port is not None:
            continue
        authority = host if port is None else f"{host}:{port}"
        urls.extend(f"http://{authority}{path}" for path in CANDIDATE_PATHS)
    return urls


class ReceiverSource:
    """Resolve a bare receiver address to a working aircraft.json URL, then fetch it.

    The resolved URL is cached after the first success. A failure once resolved does NOT
    re-probe: a receiver that answered on /tar1090 and then stopped answering is down or
    rebooting, and silently wandering to a different path would hide that.
    """

    def __init__(self, host: str, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.host = host
        self.max_bytes = max_bytes
        self.resolved_url: str | None = None
        self._candidates = candidate_urls(host)

    def _probe(self, timeout: float) -> str:
        """Try each candidate URL, returning the first that yields usable aircraft.json.

        Uses a short per-candidate timeout: six candidates at the full fetch timeout would
        stall the first frame for half a minute on an unreachable host, and a receiver on
        the same LAN answers in milliseconds or not at all.
        """
        from adsbtui.sources.http import HttpSource

        per_try = max(0.5, min(timeout, 2.0))
        errors: list[str] = []
        for url in self._candidates:
            try:
                HttpSource(url, self.max_bytes).fetch(per_try)
            except SourceError as exc:
                errors.append(f"{url}: {exc}")
                continue
            log.info("receiver %s resolved to %s", self.host, url)
            return url

        tried = "\n  ".join(errors)
        raise SourceUnreachable(
            f"no aircraft.json found on {self.host}. Tried:\n  {tried}\n"
            "If your receiver serves it somewhere else, pass the full URL instead."
        )

    def fetch(self, timeout: float) -> dict:
        from adsbtui.sources.http import HttpSource

        if self.resolved_url is None:
            self.resolved_url = self._probe(timeout)
        return HttpSource(self.resolved_url, self.max_bytes).fetch(timeout)
