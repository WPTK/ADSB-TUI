"""Key sequences (xterm-256color terminfo) and small screen helpers shared by the flows."""

from __future__ import annotations

from harness import Term

KEYS = {
    "up": b"\x1bOA",
    "down": b"\x1bOB",
    "right": b"\x1bOC",
    "left": b"\x1bOD",
    "home": b"\x1bOH",
    "end": b"\x1bOF",
    "pgup": b"\x1b[5~",
    "pgdn": b"\x1b[6~",
    "enter": b"\r",
    "esc": b"\x1b",
    "tab": b"\t",
    "backtab": b"\x1b[Z",
    "space": b" ",
    "ctrl-c": b"\x03",
    "f1": b"\x1bOP",
    "f2": b"\x1bOQ",
    "f3": b"\x1bOR",
    "f4": b"\x1bOS",
    "f5": b"\x1b[15~",
    "f6": b"\x1b[17~",
    "f7": b"\x1b[18~",
    "f8": b"\x1b[19~",
    "f10": b"\x1b[21~",
}


def press(term: Term, *names: str) -> None:
    """Send named keys (see KEYS) or literal text for any name not in the table.

    Each key is followed by a short pump so the app has read it before the next one is
    written; a burst of escape sequences can otherwise be coalesced and a key dropped.
    """
    for name in names:
        term.keys(KEYS.get(name, name.encode()))
        term.pump(0.08)


def order(term: Term, names: list[str]) -> list[str]:
    """The given row labels, sorted top to bottom as they appear on screen."""
    found = []
    for name in names:
        pos = term.find(name)
        if pos is not None:
            found.append((pos[1], name))
    return [name for _, name in sorted(found)]


def selected_hex(term: Term) -> str:
    """Hex of the row under the cursor, read by opening and closing the detail screen.

    Reverse video cannot identify the cursor: an emergency row is reverse on its own.
    """
    press(term, "enter")
    term.wait_for("Hex ")
    line = next(line for line in term.lines if "Hex " in line and ":" in line)
    hex_ = line.split(":", 1)[1].split("│")[0].strip()
    press(term, "esc")
    term.wait_for("Hex ", gone=True)
    return hex_


FIXTURE_ROWS = ["UAL123", "~A55501", "N12345", "AAL911", "SKW4455"]
