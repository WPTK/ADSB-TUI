"""Everything the user can see or change, on the main thread only.

No curses, no I/O, no threads, and it never reads a clock: ``App`` calls ``tick(now)`` once per
loop turn and every age, clock text and message expiry is computed from that value.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from adsbtui.config import Config
from adsbtui.model import Aircraft, Snapshot
from adsbtui.ui.frame import Frame
from adsbtui.ui.sorting import sort_aircraft


@dataclass
class ViewState:
    cfg: Config
    now: float = 0.0
    frame: Frame | None = None  #: newest frame, healthy or not
    last_ok: Frame | None = None  #: newest healthy frame
    rows: list[Aircraft] = field(default_factory=list)  #: what the table shows, sorted
    unsorted: list[Aircraft] = field(
        default_factory=list
    )  #: the same aircraft, as the frame had them
    _sorted_by: tuple[str, bool] | None = None
    paused: bool = False
    demo: bool = False  #: scripted traffic: the status line says so
    radar_on: bool = True
    cursor: int = 0
    #: The aircraft under the cursor. New frames move the cursor to follow it; the index is
    #: only the fallback when that aircraft has left the list.
    selected_hex: str | None = None
    scroll: int = 0
    search_text: str = ""
    #: Open screens as (name, screen). Never deeper than one today; keeps the stack shape.
    stack: list[tuple[str, Any]] = field(default_factory=list)
    flash: tuple[str, float] | None = None  #: text, expires at ``now``
    registry_note: str | None = None  #: set by App from the registry job, once per turn
    msg_rate: float | None = None
    dirty: bool = True
    _prev_messages: int | None = None
    _prev_messages_time: float | None = None

    # ------------------------------------------------------------------ time

    def tick(self, now: float) -> None:
        """Set the time for this loop turn; a new second means the clock text changes."""
        if int(now) != int(self.now):
            self.dirty = True
        self.now = now

    def clock_text(self) -> str:
        return time.strftime("%H:%M:%S", time.localtime(self.now))

    def last_success_age(self) -> float | None:
        if self.last_ok is None:
            return None
        return max(0.0, self.now - self.last_ok.fetched_at)

    # ------------------------------------------------------------------ frames

    @property
    def error(self) -> str | None:
        return self.frame.error if self.frame is not None else None

    @property
    def feed_stale(self) -> bool:
        return self.last_ok.feed_stale if self.last_ok is not None else False

    def apply(self, frame: Frame) -> None:
        """Take a frame from the queue. The only place a frame becomes view state."""
        self.frame = frame
        self.dirty = True
        if frame.error is not None:
            return
        self.last_ok = frame
        if frame.snapshot is not None:
            self._update_msg_rate(frame.snapshot)
        if not self.paused:
            self.unsorted = frame.aircraft
            self._sort()

    def resort(self) -> None:
        """Re-sort the rows if the sort key or direction changed since they were sorted."""
        display = self.cfg.display
        if self._sorted_by != (display.sort_key, display.sort_reverse):
            self._sort()
            self.dirty = True

    def _sort(self) -> None:
        display = self.cfg.display
        self._sorted_by = (display.sort_key, display.sort_reverse)
        self.rows = sort_aircraft(self.unsorted, display.sort_key, display.sort_reverse)
        self._follow_selection()

    def _update_msg_rate(self, snapshot: Snapshot) -> None:
        """Rolling messages/sec from two successive snapshots' cumulative counters. Resets to
        None (rather than going negative) if the counter ever decreases, which means the
        upstream source restarted."""
        if snapshot.messages is None:
            return
        if self._prev_messages is not None and self._prev_messages_time is not None:
            dt = snapshot.fetched_at - self._prev_messages_time
            delta = snapshot.messages - self._prev_messages
            self.msg_rate = delta / dt if dt > 0 and delta >= 0 else None
        self._prev_messages = snapshot.messages
        self._prev_messages_time = snapshot.fetched_at

    # ------------------------------------------------------------------ screens

    @property
    def screen(self) -> Any:
        return self.stack[-1][1] if self.stack else None

    @property
    def screen_name(self) -> str | None:
        return self.stack[-1][0] if self.stack else None

    def push(self, name: str, screen: Any) -> None:
        self.stack.append((name, screen))

    def pop(self) -> tuple[str, Any] | None:
        return self.stack.pop() if self.stack else None

    # ------------------------------------------------------------------ cursor

    def _follow_selection(self) -> None:
        for index, ac in enumerate(self.rows):
            if ac.hex == self.selected_hex:
                self.cursor = index
                return
        self._pin()

    def _pin(self) -> None:
        if self.rows:
            self.selected_hex = self.rows[max(0, min(self.cursor, len(self.rows) - 1))].hex
        else:
            self.selected_hex = None

    def move_cursor(self, to: int) -> None:
        """A user move: land inside the list and pin the aircraft there."""
        self.cursor = max(0, min(to, len(self.rows) - 1))
        self._pin()

    def selected(self) -> Aircraft | None:
        if not self.rows:
            return None
        return self.rows[max(0, min(self.cursor, len(self.rows) - 1))]

    def clamp(self, total: int, table_height: int) -> None:
        """Keep the cursor within the list and the scroll window around the cursor."""
        if total == 0:
            self.cursor = 0
            self.scroll = 0
            self.selected_hex = None
            return
        self.cursor = max(0, min(self.cursor, total - 1))
        self._pin()
        if table_height <= 0:
            self.scroll = 0
            return
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + table_height:
            self.scroll = self.cursor - table_height + 1
        self.scroll = max(0, min(self.scroll, max(0, total - table_height)))

    # ------------------------------------------------------------------ status note

    def note(self) -> str | None:
        """The status-line note: a recent flash message, else the registry job's progress."""
        if self.flash is not None:
            text, until = self.flash
            if self.now < until:
                return text
            self.flash = None
        return self.registry_note

    def flash_for(self, text: str, seconds: float = 6.0) -> None:
        self.flash = (text, self.now + seconds)
