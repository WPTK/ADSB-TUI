"""U03 main table, U14 pause / units / radius."""

from __future__ import annotations

import re

import fakefeed
from keys import FIXTURE_ROWS, order, press, selected_hex


def test_main_table_shows_the_fixture(spawn):
    term = spawn()
    term.wait_for("UAL123")
    assert "LIVE" in term.text
    assert "5 aircraft" in term.text
    header = next(line for line in term.lines if "FLIGHT" in line)
    assert "ALTITUDE" in header and "SPEED" in header
    assert order(term, FIXTURE_ROWS) == FIXTURE_ROWS  # nearest first


def test_pause_then_quit(spawn):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "p")
    term.wait_for("PAUSED")
    press(term, "q")
    assert term.wait_exit() == 0


def test_cursor_keys_move_the_selection(spawn):
    term = spawn()
    term.wait_for("UAL123")
    first = selected_hex(term)
    press(term, "j")
    second = selected_hex(term)
    assert second != first
    press(term, "down")
    third = selected_hex(term)
    assert third not in (first, second)
    press(term, "k")
    assert selected_hex(term) == second
    press(term, "up")
    assert selected_hex(term) == first
    press(term, "end")
    last = selected_hex(term)
    assert last not in (first, second, third)
    press(term, "home")
    assert selected_hex(term) == first
    press(term, "pgdn")
    assert selected_hex(term) == last
    press(term, "pgup")
    assert selected_hex(term) == first


def test_pause_freezes_the_list_while_the_feed_moves(spawn, feed):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "p")
    term.wait_for("PAUSED")
    feed.script(fakefeed.ok([fakefeed.ac("abc123", 37.78, -122.42, flight="NEWONE")]))
    feed.wait_requests(feed.requests + 3)
    term.pump(0.5)
    assert "NEWONE" not in term.text and "UAL123" in term.text
    press(term, "p")
    term.wait_for("NEWONE")  # the lost aircraft lingers, so UAL123 is still listed


def test_units_cycle_changes_the_cells(spawn):
    term = spawn()
    term.wait_for("5.3 mi")
    seen = {"mi"}
    for _ in range(3):
        press(term, "u")
        term.pump(0.4)
        for unit in ("mi", "km", "nm"):
            if f"{unit} " in term.text or term.text.count(f" {unit}") > 2:
                seen.add(unit)
    assert {"mi", "km", "nm"} <= seen


def _count(term) -> int:
    match = re.search(r"(\d+) aircraft", term.text)
    return int(match.group(1)) if match else -1


def test_radius_changes_the_row_count(spawn):
    term = spawn(env={"ADSBTUI_DISPLAY_LINGER_S": "1"})
    term.wait_for("5 aircraft")
    for _ in range(12):
        press(term, "-")
    term.wait_for(lambda t: 0 <= _count(t) < 5)
    for _ in range(14):
        press(term, "+")
    term.wait_for("5 aircraft")
