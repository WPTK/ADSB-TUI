"""End-to-end smoke tests that actually launch the curses UI.

Everything else in the suite tests pure functions, which is deliberate -- but it means
nothing else would catch the class of bug that plagued the original tool: an unguarded
curses call that only blows up on a real terminal, at a particular size, or on the way
out. These tests run the real console entry point inside a pseudo-terminal so curses gets
a genuine TTY, then assert the process exits cleanly rather than dumping a traceback.

Skipped on platforms without pty (Windows).
"""

from __future__ import annotations

import os
import re
import select
import signal
import struct
import sys
import time

import pytest

# These modules are POSIX-only, and a plain "import fcntl" would fail at COLLECTION time
# on Windows -- before pytest ever evaluates a skipif mark, which only guards execution.
# importorskip raises Skipped during collection instead, so the whole module is reported
# as skipped rather than erroring the run.
fcntl = pytest.importorskip("fcntl", reason="pty-based TUI smoke tests need POSIX")
pty = pytest.importorskip("pty", reason="pty-based TUI smoke tests need POSIX")
termios = pytest.importorskip("termios", reason="pty-based TUI smoke tests need POSIX")

pytestmark = pytest.mark.skipif(
    not hasattr(os, "fork") or sys.platform == "win32",
    reason="pty-based TUI smoke tests need a POSIX fork/pty",
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(REPO_ROOT, "tests", "fixtures", "aircraft.json")
ENTRY = os.path.join(REPO_ROOT, "adsbtui.py")

#: Long enough for the first fetch to land and the first frame to paint.
STARTUP_S = 2.0
#: Generous upper bound on how long a clean shutdown may take.
EXIT_TIMEOUT_S = 6.0


def _run_tui(cols: int, rows: int, keys: bytes) -> tuple[str, int | str]:
    """Launch the TUI in a pty of the given size, send `keys`, return (output, exit code).

    The exit code is the string "TIMEOUT-still-running" if the process had to be killed,
    which is itself a test failure -- a TUI that ignores its quit key is unusable.
    """
    pid, fd = pty.fork()
    if pid == 0:  # child
        os.environ["TERM"] = "xterm"
        # ncurses reads LINES/COLUMNS in preference to the terminal's ioctl size. Setting
        # them here is what makes the size deterministic: the parent's TIOCSWINSZ below
        # races against the child reaching initscr(), and losing that race silently gives
        # the child a default-sized terminal instead of the one under test.
        os.environ["LINES"] = str(rows)
        os.environ["COLUMNS"] = str(cols)
        os.chdir(REPO_ROOT)
        os.execv(
            sys.executable,
            [sys.executable, ENTRY, "--url", FIXTURE, "--lat", "37.7749", "--lon", "-122.4194"],
        )

    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    time.sleep(STARTUP_S)

    out = b""
    os.write(fd, keys)
    deadline = time.time() + EXIT_TIMEOUT_S
    while time.time() < deadline:
        readable, _, _ = select.select([fd], [], [], 0.3)
        if readable:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break  # the child exited and closed its end of the pty
            if not chunk:
                break
            out += chunk

    text = out.decode("utf-8", "replace")
    try:
        os.kill(pid, 0)
        reaped, status = os.waitpid(pid, os.WNOHANG)
        if reaped == 0:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return text, "TIMEOUT-still-running"
    except ProcessLookupError:
        return text, 0
    try:
        _, status = os.waitpid(pid, 0)
        return text, os.WEXITSTATUS(status)
    except ChildProcessError:
        return text, 0


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][B0]|\r", "", text)


def test_quits_cleanly_on_q():
    text, code = _run_tui(120, 30, b"q")
    assert "Traceback" not in text, text[-2000:]
    assert code == 0


def test_pause_then_quit():
    """Pausing must not wedge the event loop -- the quit key still has to work."""
    text, code = _run_tui(120, 30, b"p q")
    assert "Traceback" not in text, text[-2000:]
    assert code == 0


def test_ctrl_c_exits_without_traceback():
    """The original tool dumped a KeyboardInterrupt traceback through a half-restored
    curses screen; a clean exit is the fix (audit finding F26)."""
    text, code = _run_tui(80, 24, b"\x03")
    assert "Traceback" not in text, text[-2000:]
    assert code == 0


def test_narrow_terminal_does_not_crash():
    """The original tool wrote a fixed 132-column header with unguarded addstr calls and
    died with curses.error on any smaller terminal (audit findings F04/F25)."""
    text, code = _run_tui(40, 10, b"q")
    assert "Traceback" not in text, text[-2000:]
    assert code == 0


def test_renders_expected_chrome_and_drops_columns_when_narrow():
    wide, _ = _run_tui(120, 30, b"q")
    narrow, _ = _run_tui(40, 10, b"q")

    wide_clean = _strip_ansi(wide)
    narrow_clean = _strip_ansi(narrow)

    assert "ADSB-TUI" in wide_clean
    # The key bar is truncated to the terminal width, so the keys a user cannot afford to
    # lose -- help and quit -- must survive that truncation on a normal-width terminal.
    assert "Help" in wide_clean
    assert "Quit" in wide_clean
    assert "FLIGHT" in wide_clean and "FLIGHT" in narrow_clean

    # Lower-priority columns must be dropped rather than truncated on a narrow terminal.
    assert "TYPE" in wide_clean
    assert "TYPE" not in narrow_clean
