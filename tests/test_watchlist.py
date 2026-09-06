"""Tests for adsbtui.watchlist."""

from __future__ import annotations

import pytest

from adsbtui import watchlist
from adsbtui.model import Aircraft
from adsbtui.watchlist import WatchEntry


def make_aircraft(**overrides) -> Aircraft:
    defaults = dict(hex="a835af", is_icao=True)
    defaults.update(overrides)
    return Aircraft(**defaults)


# --------------------------------------------------------------------------------------
# parse_line
# --------------------------------------------------------------------------------------


def test_parse_line_hex():
    entry = watchlist.parse_line("hex:a835af")
    assert entry == WatchEntry(prefix="hex", pattern="a835af", raw_line="hex:a835af")


def test_parse_line_reg():
    entry = watchlist.parse_line("reg:N*UA")
    assert entry == WatchEntry(prefix="reg", pattern="N*UA", raw_line="reg:N*UA")


def test_parse_line_call():
    entry = watchlist.parse_line("call:SAM*")
    assert entry == WatchEntry(prefix="call", pattern="SAM*", raw_line="call:SAM*")


def test_parse_line_owner():
    entry = watchlist.parse_line("owner:*SHERIFF*")
    assert entry == WatchEntry(prefix="owner", pattern="*SHERIFF*", raw_line="owner:*SHERIFF*")


def test_parse_line_type():
    entry = watchlist.parse_line("type:C130*")
    assert entry == WatchEntry(prefix="type", pattern="C130*", raw_line="type:C130*")


def test_parse_line_comment_is_none():
    assert watchlist.parse_line("# this is a comment") is None
    assert watchlist.parse_line("   # indented comment") is None


def test_parse_line_blank_is_none():
    assert watchlist.parse_line("") is None
    assert watchlist.parse_line("   \n") is None


def test_parse_line_malformed_no_colon_raises():
    with pytest.raises(ValueError):
        watchlist.parse_line("just some garbage")


def test_parse_line_malformed_unknown_prefix_raises():
    with pytest.raises(ValueError):
        watchlist.parse_line("bogus:N123UA")


def test_parse_line_malformed_empty_pattern_raises():
    with pytest.raises(ValueError):
        watchlist.parse_line("hex:")


# --------------------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path):
    missing = tmp_path / "does_not_exist.txt"
    assert watchlist.load(str(missing)) == []


def test_load_skips_bad_lines(tmp_path, caplog):
    path = tmp_path / "watchlist.txt"
    path.write_text(
        "\n".join(
            [
                "# a comment",
                "hex:a835af",
                "",
                "bogus:oops",
                "reg:N*UA",
                "no colon here",
                "call:SAM*",
            ]
        )
    )

    with caplog.at_level("WARNING", logger="adsbtui"):
        entries = watchlist.load(str(path))

    assert [e.prefix for e in entries] == ["hex", "reg", "call"]
    assert [e.pattern for e in entries] == ["a835af", "N*UA", "SAM*"]
    # the two malformed lines should have produced warnings, not raised
    assert len(caplog.records) == 2


# --------------------------------------------------------------------------------------
# matches
# --------------------------------------------------------------------------------------


def test_matches_hex():
    aircraft = make_aircraft(hex="a835af")
    entries = [watchlist.parse_line("hex:a835af")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "hex"


def test_matches_hex_strips_leading_tilde():
    aircraft = make_aircraft(hex="~a835af", is_icao=False)
    entries = [watchlist.parse_line("hex:a835af")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "hex"


def test_matches_reg_case_insensitive():
    aircraft = make_aircraft(registration="n123ua")
    entries = [watchlist.parse_line("reg:N*UA")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "reg"


def test_matches_call():
    aircraft = make_aircraft(flight="SAM123")
    entries = [watchlist.parse_line("call:SAM*")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "call"


def test_matches_owner():
    aircraft = make_aircraft(owner_name="County Sheriff Office")
    entries = [watchlist.parse_line("owner:*SHERIFF*")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "owner"


def test_matches_type():
    aircraft = make_aircraft(type_code="C130J")
    entries = [watchlist.parse_line("type:C130*")]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "type"


def test_matches_no_match_returns_none():
    aircraft = make_aircraft(registration="N999ZZ")
    entries = [watchlist.parse_line("reg:N*UA")]
    assert watchlist.matches(aircraft, entries) is None


def test_matches_none_field_is_skipped():
    aircraft = make_aircraft(registration=None)
    entries = [watchlist.parse_line("reg:N*UA")]
    assert watchlist.matches(aircraft, entries) is None


def test_matches_empty_entries_returns_none():
    aircraft = make_aircraft()
    assert watchlist.matches(aircraft, []) is None


def test_matches_disabled_entry_is_skipped():
    aircraft = make_aircraft(registration="N123UA")
    entry = watchlist.parse_line("reg:N*UA")
    entry.enabled = False
    assert watchlist.matches(aircraft, [entry]) is None


def test_matches_returns_first_match():
    aircraft = make_aircraft(registration="N123UA", flight="SAM123")
    entries = [
        watchlist.parse_line("call:SAM*"),
        watchlist.parse_line("reg:N*UA"),
    ]
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "call"


# --------------------------------------------------------------------------------------
# add_entry / round-trip
# --------------------------------------------------------------------------------------


def test_add_entry_unknown_prefix_raises(tmp_path):
    path = tmp_path / "watchlist.txt"
    with pytest.raises(ValueError):
        watchlist.add_entry(str(path), "bogus", "N123UA")


def test_add_entry_and_load_round_trip(tmp_path):
    path = tmp_path / "nested" / "watchlist.txt"

    watchlist.add_entry(str(path), "hex", "a835af")
    watchlist.add_entry(str(path), "reg", "N*UA")

    entries = watchlist.load(str(path))
    assert [(e.prefix, e.pattern) for e in entries] == [
        ("hex", "a835af"),
        ("reg", "N*UA"),
    ]

    aircraft = make_aircraft(hex="a835af")
    result = watchlist.matches(aircraft, entries)
    assert result is not None
    assert result.prefix == "hex"
