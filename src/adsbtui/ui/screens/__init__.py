"""Screen classes for the ADSB-TUI application.

A "screen" here is any object matching this design contract:

  * a read-only ``title`` property/attribute (str)
  * ``handle_key(self, key) -> str | None``: process one keypress; return the string
    "close" to signal the screen should be popped off the navigation stack, None to stay
    open, or any other short string as an application-level signal a future integration
    step can interpret.
  * ``render_lines(self, width, height) -> list[str]``: PURE -- returns the screen's full
    content as a list of plain strings, each already clipped to the given width. No curses
    calls, so it is fully unit-testable without a terminal.
"""

from __future__ import annotations
