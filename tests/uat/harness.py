"""A real terminal for the real program: spawn `python -m adsbtui` in a pty, parse the bytes.

Every byte the child writes goes into a pyte screen, so a test asserts on what a person would
see (`screen.display`) and on cell attributes (`screen.buffer[y][x]`), not on curses calls.
That is the point of this layer: the bugs that matter live between the app and the terminal.

Rules the harness keeps so tests stay reliable:
  * Never `sleep`. `wait_for` pumps the pty until a predicate holds or a deadline passes.
  * The window size is set on the pty before the child starts (no LINES/COLUMNS), so curses
    sees the size under test from its very first ioctl.
  * The child gets a throwaway HOME, XDG dirs and cwd, so a run never reads or writes the
    developer's real config, registry database or history.
  * A failing wait dumps the screen and the raw stream tail into the assertion message and
    into `tests/uat/_artifacts/` for CI to upload.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from collections.abc import Callable
from pathlib import Path

import pyte

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path(__file__).resolve().parent / "_artifacts"

#: Generous: a loaded CI runner can be an order of magnitude slower than a laptop.
DEFAULT_TIMEOUT_S = 20.0

ALT_SCREEN_OFF = b"\x1b[?1049l"
ALT_SCREEN_ON = b"\x1b[?1049h"


class Term:
    """One child process attached to one pty, with a pyte screen mirroring it."""

    def __init__(
        self,
        argv: list[str],
        *,
        cols: int = 120,
        rows: int = 30,
        env: dict[str, str],
        cwd: Path,
        name: str = "term",
    ) -> None:
        self.cols, self.rows, self.name = cols, rows, name
        self.raw = bytearray()
        self.screen = pyte.Screen(cols, rows)
        self._stream = pyte.ByteStream(self.screen)
        self._eof = False

        self._master, slave = pty.openpty()
        self._set_winsize(cols, rows)
        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-m", "adsbtui", *argv],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=env,
                cwd=cwd,
                start_new_session=True,
                close_fds=True,
                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),  # noqa: PLW1509
            )
        finally:
            os.close(slave)

    # ------------------------------------------------------------------ plumbing

    def _set_winsize(self, cols: int, rows: int) -> None:
        fcntl.ioctl(self._master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def pump(self, wait: float = 0.05) -> bool:
        """Read whatever the child has written and feed it to the screen. True if any."""
        if self._eof:
            return False
        got = False
        while True:
            readable, _, _ = select.select([self._master], [], [], wait if not got else 0)
            if not readable:
                return got
            try:
                chunk = os.read(self._master, 65536)
            except OSError:  # Linux: EIO once the child has closed its end
                self._eof = True
                return got
            if not chunk:
                self._eof = True
                return got
            self.raw += chunk
            self._stream.feed(chunk)
            got = True

    # ------------------------------------------------------------------ input

    def keys(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        os.write(self._master, data)

    def resize(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        self._set_winsize(cols, rows)
        self.screen.resize(rows, cols)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.proc.pid, signal.SIGWINCH)

    def signal(self, sig: int) -> None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.proc.pid, sig)

    # ------------------------------------------------------------------ reading

    @property
    def text(self) -> str:
        return "\n".join(self.screen.display)

    @property
    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    def cell(self, x: int, y: int):
        return self.screen.buffer[y][x]

    def find(self, needle: str) -> tuple[int, int] | None:
        """(x, y) of the first occurrence of `needle` on screen, or None."""
        for y, line in enumerate(self.screen.display):
            x = line.find(needle)
            if x >= 0:
                return x, y
        return None

    def dump(self) -> str:
        border = "+" + "-" * self.cols + "+"
        body = "\n".join(f"|{line}|" for line in self.screen.display)
        tail = bytes(self.raw[-600:]).decode("utf-8", "replace")
        return f"{border}\n{body}\n{border}\n[raw tail] {tail!r}"

    def _fail(self, what: str) -> AssertionError:
        ARTIFACTS.mkdir(exist_ok=True)
        with contextlib.suppress(OSError):
            (ARTIFACTS / f"{self.name}.txt").write_text(f"{what}\n{self.dump()}\n", "utf-8")
        return AssertionError(f"{what}\n{self.dump()}")

    # ------------------------------------------------------------------ waiting

    def wait_for(
        self,
        what: str | Callable[[Term], bool],
        timeout: float = DEFAULT_TIMEOUT_S,
        *,
        gone: bool = False,
    ) -> None:
        """Pump until `what` is on screen (or, with gone=True, is not)."""
        if isinstance(what, str):
            needle = what

            def pred(term: Term) -> bool:
                return (needle in term.text) != gone

            label = f"{'absence' if gone else 'presence'} of {needle!r}"
        else:
            pred, label = what, "predicate"
        deadline = time.monotonic() + timeout
        while True:
            self.pump()
            if pred(self):
                return
            if time.monotonic() >= deadline:
                raise self._fail(f"timed out after {timeout}s waiting for {label}")
            if self._eof and self.proc.poll() is not None:
                self.pump(0)
                if pred(self):
                    return
                raise self._fail(f"process exited ({self.proc.returncode}) before {label}")

    def wait_exit(self, timeout: float = DEFAULT_TIMEOUT_S) -> int:
        """Drain output until the child exits; return its exit code."""
        deadline = time.monotonic() + timeout
        while self.proc.poll() is None:
            self.pump()
            if time.monotonic() >= deadline:
                raise self._fail(f"process still running after {timeout}s")
        # Collect what the child wrote on its way out.
        for _ in range(20):
            if not self.pump(0.02):
                break
        return int(self.proc.returncode)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.signal(signal.SIGKILL)
        with contextlib.suppress(Exception):
            self.proc.wait(timeout=5)
        with contextlib.suppress(OSError):
            os.close(self._master)
