"""Terminal display width for untrusted text, stdlib only.

len() counts code points, but a terminal counts cells: CJK and fullwidth characters take
two, combining marks take none. Padding and truncating by len() lets a wide callsign push a
row past the edge of its cell, and the terminal wraps it onto the next line.
"""

from __future__ import annotations

import unicodedata


def char_width(ch: str) -> int:
    """Cells one character occupies: 0 for combining marks, 2 for wide/fullwidth, else 1."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def display_width(text: str) -> int:
    """Cells the whole string occupies."""
    return sum(char_width(ch) for ch in text)


def clip(text: str, width: int) -> str:
    """The longest prefix of text that fits in width cells.

    A wide character that would straddle the edge is dropped rather than split, so the
    result can be one cell short of width; callers that need an exact width pad after.
    """
    used = 0
    out = []
    for ch in text:
        w = char_width(ch)
        if used + w > width:
            break
        out.append(ch)
        used += w
    return "".join(out)


def ljust(text: str, width: int) -> str:
    """Pad text on the right to width cells (clipping first if it is too wide)."""
    clipped = clip(text, width)
    return clipped + " " * (width - display_width(clipped))


def rjust(text: str, width: int) -> str:
    """Pad text on the left to width cells (clipping first if it is too wide)."""
    clipped = clip(text, width)
    return " " * (width - display_width(clipped)) + clipped
