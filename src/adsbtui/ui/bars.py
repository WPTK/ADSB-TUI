"""Plain-string builders for the title bar, key/legend bar, and status line.

This module is pure: no curses calls, no network calls, no global state. Each function
below returns a single line of text, already clipped so it never exceeds the caller-given
terminal width. A later, curses-aware drawing layer addstr()s the strings these functions
return inside its own try/except curses.error guard -- that separation is what makes this
module unit-testable without a terminal, and it must never import curses or call any
curses function.
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


def title_bar_text(
    width: int,
    version: str,
    paused: bool,
    units: str,
    config_path: str | None,
) -> str:
    """Build the title bar: program name/version, a PAUSED flag, units, and config path.

    e.g. "ADSB-TUI v0.9.0  PAUSED  units: imperial  config: /etc/adsbtui/config.toml",
    clipped to width characters.
    """
    parts = [f"ADSB-TUI v{version}"]
    if paused:
        parts.append("PAUSED")
    parts.append(f"units: {units}")
    parts.append(f"config: {config_path}" if config_path else "config: (none)")
    return _clip("  ".join(parts), width)


def key_bar_text(width: int, keymap: dict[str, str]) -> str:
    """Build the key/legend bar from an action-name -> key-label mapping.

    Renders in keymap's iteration order as "<key> <Action Label>" entries (the action
    name with underscores turned to spaces and title-cased, e.g. "help" -> "Help"),
    separated by two spaces -- e.g. "F1 Help  F2 Settings  ...  F10 Quit". Entries are
    added one at a time and the whole line stops growing the moment the next entry would
    not fit; trailing entries that do not fit are dropped whole, never truncated
    mid-entry, so the result never exceeds width characters.
    """
    if width <= 0:
        return ""
    text = ""
    for action, key in keymap.items():
        label = action.replace("_", " ").replace("-", " ").title()
        entry = f"{key} {label}"
        candidate = f"{text}  {entry}" if text else entry
        if len(candidate) > width:
            break
        text = candidate
    return text


def status_line_text(
    width: int,
    source_ok: bool,
    source_message: str | None,
    last_success_age_s: float | None,
    aircraft_count: int,
    alert_count: int,
    msg_rate: float | None,
) -> str:
    """Build the status line: source health, aircraft/alert counts, and message rate.

    e.g. "Source: OK  age 2s  |  41 aircraft, 2 alerts  |  120 msg/s", clipped to width
    characters. When source_ok is False and source_message is set, it is appended in
    parentheses, e.g. "Source: FAIL (connection refused)".
    """
    left = f"Source: {'OK' if source_ok else 'FAIL'}"
    if not source_ok and source_message:
        left += f" ({source_message})"
    if last_success_age_s is not None:
        left += f"  age {_format_duration(last_success_age_s)}"

    middle = f"{aircraft_count} aircraft, {alert_count} alerts"
    right = f"{msg_rate:.0f} msg/s" if msg_rate is not None else "N/A msg/s"

    return _clip(f"{left}  |  {middle}  |  {right}", width)
