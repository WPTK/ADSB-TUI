"""Windows smoke: start the TUI under ConPTY, wait for the table header, press q.

Run by the nightly workflow only (pywinpty is not a project dependency). Not collected by
pytest: the name does not start with test_.
"""

from __future__ import annotations

import sys
import time

import pyte
from winpty import PtyProcess

FIXTURE = "tests/fixtures/aircraft.json"
DEADLINE_S = 30.0


def main() -> int:
    screen = pyte.Screen(120, 40)
    stream = pyte.Stream(screen)
    proc = PtyProcess.spawn(
        [
            sys.executable,
            "-m",
            "adsbtui",
            "--url",
            FIXTURE,
            "--lat",
            "37.7749",
            "--lon",
            "-122.4194",
        ],
        dimensions=(40, 120),
    )
    end = time.monotonic() + DEADLINE_S
    seen = False
    while time.monotonic() < end and not seen:
        try:
            stream.feed(proc.read(4096))
        except EOFError:
            break
        seen = "FLIGHT" in "\n".join(screen.display)
    if not seen:
        print("FAIL: no FLIGHT header within", DEADLINE_S, "s")
        print("\n".join(screen.display))
        proc.terminate(force=True)
        return 1
    proc.write("q")
    while proc.isalive() and time.monotonic() < end:
        time.sleep(0.1)
    if proc.isalive():
        print("FAIL: still running after q")
        proc.terminate(force=True)
        return 1
    text = "\n".join(screen.display)
    if "Traceback" in text or proc.exitstatus != 0:
        print("FAIL: exit", proc.exitstatus)
        print(text)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
