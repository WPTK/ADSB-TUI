"""Hostile feed data through every sink: nothing escapes the boundary.

An aircraft.json comes from the network (or a file someone else can write), so every field
is attacker-controlled. These tests push escape sequences, NULs, bidi overrides, wide
characters and non-finite numbers through normalize and out the other side: the table, CSV
and JSON output, the detail screen, the table row, and the curses write helper.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from adsbtui import output
from adsbtui.config import build_config
from adsbtui.normalize import parse_aircraft
from adsbtui.sources.jsonfile import FileSource
from adsbtui.textwidth import display_width
from adsbtui.ui import columns as columns_mod
from adsbtui.ui.app import App
from adsbtui.ui.screens.detail import DetailScreen

ATTACKS = {
    "osc_title": "\x1b]0;PWN\x07",
    "csi_clear": "\x1b[2J\x1b[H",
    "c1_csi": "\x9b31m",
    "nul": "AB\x00CD",
    "bell": "\x07\x07",
    "rlo": "‮GNP",
    "newline": "a\r\nb",
    "cjk": "日本語日本語日本語日本語",
}

STRING_FIELDS = ["flight", "squawk", "emergency", "category", "type", "r", "t", "desc", "ownOp"]
#: Fields whose clean value is not the input text, so we only check the output is safe.
HEX_ATTACKS = ["\x1b]0;PWN\x07", "ab\x00cde", "12345\x1b", "~\x1b[2Jab", "zzzzzz", ""]

FORBIDDEN = {chr(c) for c in range(0x20)} | {chr(0x7F)} | {chr(c) for c in range(0x80, 0xA0)}


def _dirty(text: str, allow: str = "") -> set[str]:
    return {ch for ch in text if ch in FORBIDDEN and ch not in allow}


def _record(**fields) -> dict:
    return {"hex": "abc123", "lat": 37.7, "lon": -122.4, "alt_baro": 3000, "gs": 200, **fields}


def _sinks(ac) -> dict[str, str]:
    """Every textual place an Aircraft is rendered, as plain strings."""
    out = {
        "table": output.render_table([ac], "imperial"),
        "csv": output.render_csv([ac], "imperial"),
        "json": output.render_json([ac], "imperial"),
    }
    detail = DetailScreen(ac, "imperial")
    out["detail"] = "\n".join(detail.render_lines(100, 60)) + detail.title
    keys = list(columns_mod.COLUMN_SPECS)
    widths = columns_mod.compute_widths(keys, 400, 30)
    out["row"] = columns_mod.format_row(ac, keys, widths, "imperial", False)
    return out


@pytest.mark.parametrize("field", STRING_FIELDS)
@pytest.mark.parametrize("attack", ATTACKS.values(), ids=ATTACKS.keys())
def test_string_field_attack_never_reaches_a_sink(field, attack):
    ac = parse_aircraft(_record(**{field: attack}))
    assert ac is not None
    for name, text in _sinks(ac).items():
        # CSV/table/JSON legitimately contain newlines between rows.
        assert not _dirty(text, allow="\n"), (name, field, _dirty(text, allow="\n"))
        assert "‮" not in text, (name, field)


@pytest.mark.parametrize("attack", HEX_ATTACKS)
def test_hex_attack_drops_the_record_or_is_clean(attack):
    ac = parse_aircraft(_record(hex=attack))
    if ac is not None:
        assert ac.hex == ac.hex.lower()
        assert not _dirty(ac.hex)


def test_registration_and_type_cannot_crash_the_detail_screen_with_a_nul():
    ac = parse_aircraft(_record(r="N1\x002", t="B7\x0038", squawk="12\x0034"))
    assert ac is not None
    lines = DetailScreen(ac, "imperial").render_lines(80, 40)
    assert all("\x00" not in line for line in lines)


@pytest.mark.parametrize(
    "literal", ["1e999", "-1e999", "Infinity", "-Infinity", "NaN"], ids=lambda s: s
)
@pytest.mark.parametrize("field", ["lat", "lon", "gs", "alt_baro", "track", "baro_rate", "seen"])
def test_non_finite_numbers_do_not_take_out_the_tick(tmp_path: Path, field, literal):
    good = '{"hex": "a00001", "lat": 37.78, "lon": -122.42, "alt_baro": 3000, "gs": 200}'
    bad = (
        f'{{"hex": "a00002", "lat": 37.79, "lon": -122.41, "alt_baro": 3000, "{field}": {literal}}}'
    )
    path = tmp_path / "aircraft.json"
    path.write_text(f'{{"now": 1.0, "aircraft": [{good}, {bad}]}}', encoding="utf-8")
    cfg = build_config(
        toml_dict={},
        env={},
        cli={
            "source.url": str(path),
            "home.lat": 37.7749,
            "home.lon": -122.4194,
            "watchlist.path": "",
            "logging.file": "",
        },
    )
    aircraft, err = output.fetch_and_process(FileSource(str(path), 8_000_000), cfg)
    assert err is None
    assert "a00001" in {a.hex for a in aircraft}
    for name, text in _sinks_for_all(aircraft).items():
        assert not re.search(r"(inf|nan)", text, re.IGNORECASE), name


def _sinks_for_all(aircraft) -> dict[str, str]:
    return {
        "table": output.render_table(aircraft, "imperial"),
        "csv": output.render_csv(aircraft, "imperial"),
        "json": output.render_json(aircraft, "imperial"),
    }


def test_out_of_range_values_become_unknown_not_crashes():
    ac = parse_aircraft(_record(lat=500, gs=-5, track=720, alt_baro=9e9))
    assert ac is not None
    assert ac.lat is None and ac.lon is None
    assert ac.ground_speed_kt is None
    assert ac.track_deg is None
    assert ac.altitude_ft is None


def test_inf_ground_speed_never_renders_as_inf():
    ac = parse_aircraft(_record(gs=float("inf")))
    assert ac is not None
    assert "inf" not in output.render_table([ac], "imperial").lower()


class _Window:
    """A stand-in for a curses window whose addstr rejects text like the real one."""

    def addstr(self, y, x, text, attr=0):
        if "\x00" in text:
            raise ValueError("embedded null character")


def test_safe_addstr_swallows_an_embedded_nul():
    App._safe_addstr(None, _Window(), 0, 0, "a\x00b")  # type: ignore[arg-type]


def test_csv_lines_end_in_a_bare_newline():
    ac = parse_aircraft(_record())
    assert ac is not None
    text = output.render_csv([ac], "imperial")
    assert "\r" not in text
    assert text.endswith("\n")


@pytest.mark.parametrize("width", [3, 6, 9, 10])
def test_a_cjk_callsign_fills_its_cell_exactly(width):
    cell = columns_mod.sanitize_cell("日本語日本語日本語", width)
    assert display_width(cell) == width


def test_a_wide_character_is_never_split_at_the_cell_edge():
    cell = columns_mod.sanitize_cell("A日本", 4)
    assert display_width(cell) == 4
    assert cell == "A日 "


def test_json_output_is_standard_json_even_for_hostile_input():
    ac = parse_aircraft(_record(gs=float("nan"), ownOp="\x1b[2Jowner"))
    assert ac is not None
    rows = json.loads(output.render_json([ac], "imperial"))
    assert rows[0]["hex"] == "abc123"
