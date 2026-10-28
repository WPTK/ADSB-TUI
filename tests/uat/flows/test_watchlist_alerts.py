"""U09 watchlist and alerts, U10 history."""

from __future__ import annotations

import sqlite3
import sys
import textwrap
from pathlib import Path

import fakefeed
import pytest
from fakefeed import HOME_LAT, HOME_LON, ac
from keys import press

#: Degrees of latitude per mile.
_MILE = 1 / 69.0


def _recorder(sandbox: Path) -> tuple[dict[str, str], Path]:
    """An alert command that appends 'HEX CALL' to a file: the fake dispatcher."""
    log = sandbox / "alerts.log"
    script = sandbox / "record_alert.py"
    script.write_text(
        textwrap.dedent(
            f"""
            import os
            with open({str(log)!r}, "a") as fh:
                fh.write(os.environ["HEX"] + " " + os.environ["CALL"] + "\\n")
            """
        ),
        "utf-8",
    )
    env = {
        "ADSBTUI_ALERTS_COMMAND": f"{sys.executable} {script}",
        "ADSBTUI_ALERTS_BELL": "false",
    }
    return env, log


def _flight_path(miles_north: list[float], **fields):
    """A callable feed step: each request returns the next position, due north of home."""
    state = {"i": 0}

    def positions():
        i = min(state["i"], len(miles_north) - 1)
        state["i"] += 1
        return [ac("abc123", HOME_LAT + miles_north[i] * _MILE, HOME_LON, flight="MOVER", **fields)]

    return positions


def test_inbound_then_overhead_dispatches_an_event(spawn, feed, sandbox):
    env, log = _recorder(sandbox)
    # Heading due south at 300 kt from 25 miles out, then parked 2 miles from home.
    feed.script(
        fakefeed.ok(_flight_path([25, 20, 15, 10, 6, 2], track=180.0, gs=300.0)),
    )
    term = spawn(env=env)
    term.wait_for("INBND")
    term.wait_for("OVHD")
    term.wait_for(lambda t: log.exists() and "abc123 MOVER" in log.read_text("utf-8"))


@pytest.mark.xfail(strict=True, reason="A06: OUTBOUND can never be reached from real data")
def test_outbound_follows_overhead(spawn, feed):
    feed.script(
        fakefeed.ok(_flight_path([6, 3, 1, 3, 6, 9], track=0.0, gs=300.0)),
    )
    term = spawn()
    term.wait_for("OVHD")
    term.wait_for("OUTBD", timeout=15)


@pytest.mark.xfail(
    strict=True, reason="A07: a stale aircraft inside the radius is still dispatched"
)
def test_stale_aircraft_is_never_dispatched(spawn, feed, sandbox):
    env, log = _recorder(sandbox)
    feed.script(
        fakefeed.ok([ac("abc123", HOME_LAT + 2 * _MILE, HOME_LON, flight="GHOST", seen_pos=120.0)]),
    )
    term = spawn(env=env)
    term.wait_for("GHOST")
    feed.wait_requests(feed.requests + 5)
    term.pump(1.0)
    assert not log.exists() or "GHOST" not in log.read_text("utf-8")


def test_watchlist_entry_added_in_the_ui_is_saved_and_matches(spawn, sandbox):
    path = sandbox / "watch.txt"
    term = spawn(env={"ADSBTUI_WATCHLIST_PATH": str(path)})
    term.wait_for("UAL123")
    press(term, "w")
    term.wait_for("Add new entry")
    press(term, "a")  # focus the add form
    press(term, "tab", "a1b2c3", "enter")
    term.wait_for("[x] hex:a1b2c3")
    press(term, "esc")  # the file is written when the screen closes
    term.wait_for("Add new entry", gone=True)
    term.wait_for(lambda t: path.exists() and "a1b2c3" in path.read_text("utf-8").lower())


def test_a_sighting_is_written_when_an_aircraft_vanishes(spawn, feed, sandbox):
    db = sandbox / "history.sqlite"
    term = spawn(
        env={
            "ADSBTUI_HISTORY_DB": str(db),
            "ADSBTUI_DISPLAY_LINGER_S": "1",
            "ADSBTUI_DISPLAY_STALE_AFTER_S": "2",
        }
    )
    term.wait_for("UAL123")
    feed.script(fakefeed.ok([]))
    term.wait_for("0 aircraft")

    def recorded(_term) -> bool:
        if not db.exists():
            return False
        with sqlite3.connect(db) as conn:
            return conn.execute("SELECT COUNT(*) FROM sightings").fetchone()[0] > 0

    term.wait_for(recorded, timeout=20)
