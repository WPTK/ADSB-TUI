"""Plain-string builders for the status line and the key bar.

This module is pure: no curses calls, no network calls, no global state. Each function
returns a single line of text, already clipped so it never exceeds the caller-given
terminal width. A later, curses-aware drawing layer addstr()s the strings these functions
return inside its own try/except curses.error guard.

There is deliberately no "title bar" any more. The old one spent a full row of a terminal
on the program's own name, its version, the current unit system and the path of the config
file -- four things that never change while you watch, sitting above the one thing that
does. The top row now carries live state only, and the key hints moved to the bottom where
they are out of the way of the table.
"""

from __future__ import annotations


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width]


def _format_duration(seconds: float | None) -> str:
    """Compact duration formatting: "N/A", "<60>s", "<60>m", or "<N>h"."""
    if seconds is None:
        return "N/A"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    return f"{hours}h"


def status_line_text(
    width: int,
    source_ok: bool,
    source_message: str | None,
    last_success_age_s: float | None,
    aircraft_count: int,
    alert_count: int,
    msg_rate: float | None,
    paused: bool = False,
    clock: str | None = None,
    note: str | None = None,
) -> str:
    """Build the one live-state line: link health, what is on screen, and any running job.

    Reads left to right in order of what you would check first: whether the feed is alive,
    then how much traffic, then the housekeeping. Fields that have nothing to say are left
    out entirely rather than printed as "N/A" -- an empty slot is quieter than a filler
    word, and this line is glanced at, not read.

    e.g. "LIVE 2s   14 aircraft  2 alerts   120 msg/s   21:04:11"
         "NO DATA (connection refused)   0 aircraft   registry 45%"
    """
    parts: list[str] = []

    if paused:
        parts.append("PAUSED")

    if source_ok:
        age = _format_duration(last_success_age_s) if last_success_age_s is not None else ""
        parts.append(f"LIVE {age}".strip())
    else:
        failed = "NO DATA"
        if source_message:
            # A multi-line probe error would wreck the layout; keep the first line only.
            first_line = str(source_message).splitlines()[0]
            failed += f" ({first_line})"
        parts.append(failed)

    traffic = f"{aircraft_count} aircraft"
    if alert_count:
        traffic += f"  {alert_count} alert" + ("s" if alert_count != 1 else "")
    parts.append(traffic)

    if msg_rate is not None:
        parts.append(f"{msg_rate:.0f} msg/s")
    if note:
        parts.append(note)
    if clock:
        parts.append(clock)

    return _clip("   ".join(parts), width)


def key_bar_text(width: int, keymap: dict[str, str]) -> str:
    """Build the bottom key bar from an action-name -> key-label mapping.

    Renders as "<key> <Action>" pairs separated by a wide gap. The gap is what the old
    two-space version got wrong: with single spaces inside a pair and two between pairs,
    "F2 Settings F8 Data" reads as one run-on string and the eye cannot find the breaks.

    Entries are added whole and the line stops growing the moment the next one would not
    fit, so a narrow terminal shows fewer keys rather than half of one.
    """
    if width <= 0:
        return ""
    text = ""
    for action, key in keymap.items():
        label = action.replace("_", " ").replace("-", " ").title()
        entry = f"{key} {label}"
        candidate = f"{text}    {entry}" if text else entry
        if len(candidate) > width:
            break
        text = candidate
    return text
