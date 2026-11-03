"""The one object that crosses from the fetch thread to the main thread."""

from __future__ import annotations

from dataclasses import dataclass

from adsbtui.model import Aircraft, Snapshot


@dataclass(frozen=True)
class Frame:
    """The result of one poll. Built on the fetch thread, never mutated after it is published.

    An error frame is ``Frame(seq, fetched_at, None, [], error, False)``.
    """

    seq: int  #: 1, 2, 3 ...; lets caches key on identity cheaply
    fetched_at: float  #: the injected clock when the tick ran: this frame's "now"
    snapshot: Snapshot | None  #: feed header (messages, feed timestamp)
    aircraft: list[Aircraft]  #: what the table shows, already filtered and sorted
    error: str | None  #: None = healthy frame
    feed_stale: bool  #: the feed answers but its data has stopped moving
