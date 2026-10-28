"""U04 sort, U05 filters, U06 columns, U07 detail, U08 radar, U13 help."""

from __future__ import annotations

import fakefeed
import pytest
from fakefeed import HOME_LAT, HOME_LON, ac
from keys import FIXTURE_ROWS, order, press

# ---------------------------------------------------------------------------- U04 sort


def test_sort_by_callsign_then_reverse_then_cancel(spawn):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "s")
    term.wait_for("Sort by")
    press(term, "space", "space", "enter")  # Space cycles the choice: Altitude, Callsign
    term.wait_for("Sort by", gone=True)
    want = sorted(FIXTURE_ROWS)
    term.wait_for(lambda t: order(t, FIXTURE_ROWS) == want)

    press(term, "S")
    term.wait_for(lambda t: order(t, FIXTURE_ROWS) == want[::-1])

    press(term, "s")
    term.wait_for("Sort by")
    press(term, "space", "esc")  # Esc cancels: the order must not change
    term.wait_for("Sort by", gone=True)
    assert order(term, FIXTURE_ROWS) == want[::-1]


# -------------------------------------------------------------------------- U05 filters

#: A filtered-out aircraft lingers like a lost one (finding N2), so keep the window short.
FAST_LINGER = {"ADSBTUI_DISPLAY_LINGER_S": "1"}


def test_search_filter_narrows_to_one_row(spawn):
    term = spawn(env=FAST_LINGER)
    term.wait_for("UAL123")
    press(term, "f")
    term.wait_for("Search")
    press(term, "SKW", "enter")
    term.wait_for("Min altitude", gone=True)
    # The count line and the rows are drawn in one frame, but wait on all of it (A44).
    term.wait_for(
        lambda t: "1 aircraft" in t.text and "SKW4455" in t.text and "UAL123" not in t.text
    )


@pytest.mark.xfail(strict=True, reason="N1: Esc in Filters still applies the typed values")
def test_escape_keeps_the_old_filter(spawn, feed):
    term = spawn(env=FAST_LINGER)
    term.wait_for("UAL123")
    press(term, "f")
    term.wait_for("Search")
    press(term, "SKW", "esc")
    term.wait_for("Min altitude", gone=True)
    feed.wait_requests(feed.requests + 8)  # long enough for a wrongly applied filter to bite
    term.pump(0.3)
    assert "5 aircraft" in term.text


def test_hide_ground_and_non_icao(spawn):
    term = spawn(env=FAST_LINGER)
    term.wait_for("N12345")
    press(term, "f")
    term.wait_for("Hide ground")
    press(term, "tab", "space", "tab", "space", "enter")  # hide ground, drop non-ICAO
    term.wait_for("Min altitude", gone=True)
    term.wait_for("N12345", gone=True)
    term.wait_for("~A55501", gone=True)
    assert "UAL123" in term.text


# --------------------------------------------------------------------------- U06 columns


def test_hiding_a_column_changes_the_header(spawn):
    term = spawn()
    term.wait_for("TAIL")
    press(term, "c")
    term.wait_for("Columns (Space")
    press(term, "down", "space", "enter")  # FLIGHT -> TAIL, toggle off, apply
    term.wait_for("Columns (Space", gone=True)
    term.wait_for(lambda t: "TAIL" not in t.text and "FLIGHT" in t.text)


def test_narrow_terminal_drops_columns_by_priority(spawn):
    wide = spawn(160, 30)
    wide.wait_for("TAIL")
    assert "CLOSEST PASS" in wide.text or "OWNER" in wide.text
    narrow = spawn(50, 20)
    assert "FLIGHT" in narrow.text and "OWNER" not in narrow.text
    assert "TAIL" not in narrow.text or "ALTITUDE" not in narrow.text


# --------------------------------------------------------------------------- U07 detail


def test_detail_fields_match_the_fixture(spawn):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "home", "enter")
    term.wait_for("Aircraft UAL123")
    for field in ("A1B2C3", "529 mph", "35,000ft", "1234", "Large"):
        assert field in term.text, field
    press(term, "esc")
    term.wait_for("Aircraft UAL123", gone=True)


def test_bearing_just_under_360_reads_north_000(spawn, feed):
    # 0.1 degree north of home and 0.0009 degree west: a bearing of about 359.6.
    feed.script(fakefeed.ok([ac("abc123", HOME_LAT + 0.1, HOME_LON - 0.00088, flight="NORTHISH")]))
    term = spawn()
    term.wait_for("NORTHISH")
    press(term, "enter")
    term.wait_for("Bearing")
    line = next(line for line in term.lines if "Bearing" in line)
    assert "N 000" in line and "360" not in line, line


HOSTILE = {
    "flight": "\x1b]0;PWN\x07ev\x1b[2Jil",
    "r": "\x1b[31mRED",
    "t": "B7\x0038",
    "desc": "\x9b31mC1",
    "ownOp": "日本語日本語日本語‮GNP",
}


def test_hostile_record_never_reaches_the_terminal(spawn, feed):
    feed.script(fakefeed.ok([ac("abc123", HOME_LAT + 0.05, HOME_LON, **HOSTILE)]))
    term = spawn()
    term.wait_for(lambda t: "3.4 mi" in t.text or "3.5 mi" in t.text)
    press(term, "enter")
    term.wait_for("Hex ")
    assert b"\x1b]0;" not in bytes(term.raw)
    assert "Traceback" not in term.text
    assert "‮" not in term.text
    press(term, "esc", "q")
    assert term.wait_exit() == 0


# ----------------------------------------------------------------------------- U08 radar


def test_radar_shows_at_wide_terminals_and_toggles(spawn):
    term = spawn(130, 30)
    term.wait_for("range 15")
    assert "⌂" in term.text  # the home marker
    press(term, "r")
    term.wait_for("range 15", gone=True)
    press(term, "r")
    term.wait_for("range 15")


def test_radar_is_off_when_the_terminal_is_narrow(spawn):
    term = spawn(90, 30)
    term.wait_for("UAL123")
    term.pump(0.5)
    assert "range 15" not in term.text


# ------------------------------------------------------------------------------ U13 help


def test_help_opens_lists_keys_and_any_key_closes(spawn):
    term = spawn()
    term.wait_for("UAL123")
    for opener in ("?", "f1"):
        press(term, opener)
        term.wait_for("any other key closes")
        assert "Quit" in term.text and "Settings" in term.text
        press(term, "x")
        term.wait_for("any other key closes", gone=True)


def test_help_lists_every_bound_key(spawn):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "?")
    term.wait_for("any other key closes")
    for needle in ("Down/j", "Home", "PgUp", "Reverse"):
        assert needle in term.text, needle


def test_help_scrolls_to_the_glossary_at_80x24(spawn):
    term = spawn(80, 24)
    term.wait_for("UAL123")
    press(term, "?")
    term.wait_for("any other key closes")
    press(term, "pgdn", "pgdn", "pgdn")
    term.wait_for("CLOSEST PASS")
