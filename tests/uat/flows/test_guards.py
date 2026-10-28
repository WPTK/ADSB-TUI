"""Guard flows for the D5 split (B2-1): the gaps in the first UAT pass.

Every key in the key table, the row colour map, overlays with resizes, the selection after the
list changes, the bell, the registry swap, ASCII borders and message expiry. None of these
were asserted before, and each one lives on a seam the split moves.
"""

from __future__ import annotations

import time

import fakefeed
import pytest
from fakefeed import HOME_LAT, HOME_LON, ac
from keys import order, press, selected_hex

from adsbtui.ui.keys import BINDINGS

_MILE = 1 / 69.0  # degrees of latitude per mile


def _north(miles: float) -> tuple[float, float]:
    return HOME_LAT + miles * _MILE, HOME_LON


def _row_y(term, label: str) -> int:
    pos = term.find(label)
    assert pos is not None, f"{label} is not on screen\n{term.dump()}"
    return pos[1]


# ------------------------------------------------------------------------- G-K every key


def test_every_screen_key_opens_its_screen(spawn):
    term = spawn()
    term.wait_for("UAL123")
    opens = {
        "help": ("Quit", ["f1", "?"]),
        "settings": ("Refresh (s)", ["f2", ","]),
        "data": ("Aircraft registry sources", ["f8", "D"]),
        "detail": ("Aircraft UAL123", ["enter", "d"]),
        "sort": ("Sort by", ["s", "f5"]),
        "filters": ("Search", ["f", "/", "f3", "f4"]),
        "columns": ("Columns (Space", ["c", "f6"]),
        "watchlist": ("Add new entry", ["w", "f7"]),
    }
    assert set(opens) == {b.action for b in BINDINGS if b.action in opens}
    for action, (marker, keys) in opens.items():
        for key in keys:
            press(term, "home", key)
            term.wait_for(marker)
            press(term, "esc")
            if action == "help":  # any key closes Help; Esc is one of them
                term.wait_for("any other key closes", gone=True)
            else:
                term.wait_for(marker, gone=True)


def test_every_other_key_acts(spawn):
    term = spawn(130, 30)
    term.wait_for("range 15")

    # Pause: p P
    for key in ("p", "P"):
        press(term, key)
        term.wait_for("PAUSED")
        press(term, key)
        term.wait_for("PAUSED", gone=True)

    # Radar: r R
    for key in ("r", "R"):
        press(term, key)
        term.wait_for("range 15", gone=True)
        press(term, key)
        term.wait_for("range 15")

    # Radius: + = grow the range, - _ shrink it
    for key, want in (("+", "range 16"), ("=", "range 17"), ("-", "range 16"), ("_", "range 15")):
        press(term, key)
        term.wait_for(want)

    # Units: u cycles the scope caption too
    press(term, "u")
    term.wait_for("range 15 km")
    press(term, "u")
    term.wait_for("range 15 nm")
    press(term, "u")
    term.wait_for("range 15 mi")

    # Reverse: S flips the order
    # Both rows must be on screen before their order means anything (find() is None mid-redraw).
    pair = ["UAL123", "SKW4455"]
    term.wait_for(lambda t: order(t, pair) == pair)
    press(term, "S")
    term.wait_for(lambda t: order(t, pair) == pair[::-1])


@pytest.mark.parametrize("key", ["q", "Q", "f10"])
def test_every_quit_key_exits_cleanly(spawn, key):
    term = spawn()
    term.wait_for("UAL123")
    press(term, key)
    assert term.wait_exit() == 0


# ------------------------------------------------------------------------ G-C colour map

COLOUR_FLEET = [
    ("OVHDROW", dict(lat=_north(1)[0], alt_baro=5000)),
    ("EMERROW", dict(lat=_north(3)[0], alt_baro=8000, squawk="7700")),
    ("MILROW", dict(lat=_north(4)[0], alt_baro=30000, dbFlags=1)),
    ("WATCHROW", dict(lat=_north(5)[0], alt_baro=30000)),
    ("STALEROW", dict(lat=_north(6)[0], alt_baro=30000, seen_pos=120.0)),
    ("LOWROW", dict(lat=_north(7)[0], alt_baro=1500)),
    ("MIDROW", dict(lat=_north(8)[0], alt_baro=6000)),
    ("HIGHROW", dict(lat=_north(9)[0], alt_baro=18000)),
    ("VHIGHROW", dict(lat=_north(10)[0], alt_baro=38000)),
    ("GNDROW", dict(lat=_north(11)[0], alt_baro="ground")),
    ("INBDROW", dict(lat=_north(14)[0], alt_baro=20000, track=180.0, gs=400.0)),
    ("ENDROW", dict(lat=_north(14.5)[0], alt_baro=30000)),  # the cursor row, drawn reversed
]


def _colour_feed():
    records = []
    for i, (flight, fields) in enumerate(COLOUR_FLEET):
        lat = fields.pop("lat")
        # No ground speed: only the rows meant to be inbound get an alert of that kind.
        fields = {"track": 90.0, "gs": None, **fields}
        records.append(ac(f"c0{i:04x}", lat, HOME_LON, flight=flight, **fields))
    return records


def test_row_colours_follow_the_alert_and_altitude_bands(spawn, feed, sandbox):
    watch = sandbox / "watch.txt"
    watch.write_text("call:WATCHROW\n", "utf-8")
    feed.script(fakefeed.ok(_colour_feed()))
    term = spawn(env={"ADSBTUI_WATCHLIST_PATH": str(watch), "ADSBTUI_ALERTS_BELL": "false"})
    term.wait_for("ENDROW")
    press(term, "end")  # the cursor row is drawn reversed; keep it off the rows under test
    term.pump(0.3)

    def style(label: str):
        cell = term.cell(0, _row_y(term, label))
        return cell.fg, cell.bold, cell.reverse

    assert style("EMERROW") == ("red", True, True)
    assert style("MILROW") == ("magenta", True, False)
    assert style("WATCHROW") == ("green", True, False)
    assert style("OVHDROW") == ("brown", True, False)  # pyte names SGR 33 "brown"
    assert style("INBDROW") == ("brown", False, False)
    assert style("STALEROW")[0] == "default"  # not the 30,000 ft band's colour
    assert style("LOWROW")[0] == "red"
    assert style("MIDROW")[0] == "brown"
    assert style("HIGHROW")[0] == "green"
    assert style("VHIGHROW")[0] == "cyan"
    assert style("GNDROW")[0] == "default"
    assert style("ENDROW")[2] is True  # selected


# ----------------------------------------------------------- G-O overlay plus resize


def test_resizing_with_an_overlay_open_redraws_the_box(spawn):
    term = spawn(120, 30)
    term.wait_for("UAL123")
    press(term, "?")
    term.wait_for("any other key closes")
    for cols, rows in ((80, 24), (60, 14), (150, 40)):
        term.resize(cols, rows)
        term.wait_for(lambda t: "any other key closes" in t.text and "aircraft" in t.lines[0])
        top = next(i for i, line in enumerate(term.lines) if "┌" in line)
        bottom = max(i for i, line in enumerate(term.lines) if "└" in line)
        assert 0 <= top < bottom < rows, term.dump()
        # No leftover border from the previous geometry outside the new box.
        box_cols = {
            x for line in term.lines[top : bottom + 1] for x, ch in enumerate(line) if ch in "┌┐└┘"
        }
        assert len(box_cols) == 2, term.dump()
    press(term, "x")
    term.wait_for("any other key closes", gone=True)


def test_an_overlay_below_the_minimum_size_still_takes_the_keys(spawn):
    term = spawn(40, 7, wait_ready=False)
    term.wait_for("Terminal too small")
    press(term, "?")  # opens Help though nothing can be drawn
    term.pump(0.3)
    assert "Terminal too small" in term.text and "Quit" not in term.text
    press(term, "x")  # ...and this key closes it again rather than being a table key
    term.resize(120, 30)
    term.wait_for("UAL123")
    assert "any other key closes" not in term.text
    press(term, "q")
    assert term.wait_exit() == 0


# ------------------------------------------------------------------- G-S selection pin


def test_the_selection_stays_on_its_aircraft_when_a_nearer_one_appears(spawn, feed):
    term = spawn()
    term.wait_for("UAL123")
    press(term, "down")
    before = selected_hex(term)
    nearer = ac("bbbbbb", *_north(0.3), flight="NEARBY1", alt_baro=3000)
    feed.script(fakefeed.ok(fakefeed.fixture_aircraft() + [nearer]))
    term.wait_for("NEARBY1")
    assert selected_hex(term) == before


# --------------------------------------------------------------------------- G-B bell


def _bell_count(term) -> int:
    return bytes(term.raw).count(b"\x07")


def test_an_alert_sounds_the_bell_on_the_main_thread(spawn, feed):
    term = spawn(env={"ADSBTUI_ALERTS_BELL": "true"})
    term.wait_for("UAL123")
    base = _bell_count(term)
    feed.script(
        fakefeed.ok(
            fakefeed.fixture_aircraft() + [ac("bbbbbb", *_north(1), flight="SOS1", squawk="7700")]
        )
    )
    term.wait_for(lambda t: _bell_count(t) > base)


def test_no_bell_when_it_is_switched_off(spawn, feed):
    term = spawn(env={"ADSBTUI_ALERTS_BELL": "false"})
    term.wait_for("UAL123")
    feed.script(
        fakefeed.ok(
            fakefeed.fixture_aircraft() + [ac("bbbbbb", *_north(1), flight="SOS1", squawk="7700")]
        )
    )
    term.wait_for("SOS1")
    term.pump(1.0)
    assert _bell_count(term) == 0


# ------------------------------------------------------------------ G-R registry refresh


def test_a_background_registry_download_shows_progress_and_enriches(spawn):
    server = fakefeed.serve_registry(delay_s=1.5)
    try:
        url = f"http://127.0.0.1:{server.server_port}/aircraft.csv.gz"
        term = spawn(
            env={
                "ADSBTUI_REGISTRY_AUTO_UPDATE": "true",
                "ADSBTUI_REGISTRY_TAR1090_URL": url,
            }
        )
        term.wait_for("registry")  # the status-line note while the job runs
        term.wait_for("N123UA")  # the enricher swapped on the main thread
        term.wait_for(lambda t: "registry " not in t.lines[0])
    finally:
        server.shutdown()


# ------------------------------------------------------------------------ G-A ASCII borders


def test_ascii_borders_reach_the_table_overlay_and_scope(spawn):
    term = spawn(130, 30, env={"ADSBTUI_DISPLAY_BORDERS": "ascii"})
    term.wait_for("range 15")
    press(term, "?")
    term.wait_for("any other key closes")
    text = term.text
    for glyph in "─│┌┐└┘├┤┬┴┼⌂":
        assert glyph not in text, f"{glyph!r} drawn with borders = ascii"
    assert "+" in text and "-" * 20 in text and "|" in text
    assert "." in text  # the scope's rings


# ---------------------------------------------------------------------- G-F flash expiry


def test_a_flash_message_expires(spawn):
    term = spawn()
    term.wait_for("UAL123")
    press(term, ",")
    term.wait_for("Refresh (s)")
    press(term, "f2")  # apply and save
    term.wait_for("settings saved")
    shown_at = time.monotonic()
    term.wait_for("settings saved", gone=True, timeout=30)
    # Lasts 6 s from when it was set, not one redraw. The clock starts when the poll first
    # sees it, which under load can be a couple of seconds late, so the bound is loose.
    assert time.monotonic() - shown_at > 2.0
