"""U18 feed faults: each fault shows a banner, keeps the last good list, and recovers."""

from __future__ import annotations

import fakefeed
import pytest
from fakefeed import drip, huge, ok, stall


def _faults():
    return {
        "http500": fakefeed.http_error(500),
        "stall": stall(120),
        "drip": drip(120),
        "bad_json": fakefeed.bad_json(),
        "huge": huge(9_000_000),
        "not_an_object": fakefeed.raw(b"[1, 2, 3]"),
    }


@pytest.mark.parametrize("name", list(_faults()))
def test_fault_shows_a_banner_keeps_the_list_and_recovers(spawn, feed, name):
    term = spawn()
    term.wait_for("UAL123")
    feed.script(_faults()[name])
    term.wait_for("NO DATA")
    assert "UAL123" in term.text  # the last good list stays on screen
    feed.script(ok(fakefeed.fixture_aircraft))
    term.wait_for("LIVE")
    term.wait_for("NO DATA", gone=True)


def test_frozen_feed_is_reported_stale(spawn, feed):
    term = spawn()
    term.wait_for("LIVE")
    feed.script(fakefeed.frozen(fakefeed.fixture_aircraft()))
    term.wait_for("STALE FEED", timeout=10)
