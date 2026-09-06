"""Tests for adsbtui.ui.columns: pure table layout and cell formatting."""

from __future__ import annotations

from adsbtui.model import Aircraft, AlertLevel
from adsbtui.ui.columns import (
    COLUMN_SPECS,
    DEFAULT_COLUMNS,
    compute_widths,
    format_header,
    format_row,
    layout,
    sanitize_cell,
)


def make_aircraft(**overrides) -> Aircraft:
    """Build a minimal Aircraft with sane defaults, overridden per test."""
    defaults = dict(hex="a1b2c3", is_icao=True)
    defaults.update(overrides)
    return Aircraft(**defaults)


def full_aircraft() -> Aircraft:
    """An aircraft with every field the column builders touch populated."""
    return make_aircraft(
        flight="UAL123",
        registration="N12345",
        type_code="B738",
        altitude_ft=35000.0,
        on_ground=False,
        baro_rate_fpm=1200.0,
        geom_rate_fpm=1150.0,
        ground_speed_kt=420.0,
        distance_mi=12.3,
        bearing_deg=270.0,
        cpa_distance_mi=0.5,
        cpa_seconds=120.0,
        owner_name="Example Airlines LLC",
        db_flags=0b0011,  # MIL + INT
        category="A5",
        seen_pos_s=3.0,
        alert_level=AlertLevel.OVERHEAD,
    )


def sep_for(ascii_only: bool) -> str:
    return "|" if ascii_only else "│"


def expected_total_len(columns: list[str], widths: dict[str, int]) -> int:
    return sum(widths[c] for c in columns) + max(0, len(columns) - 1)


def split_by_widths(row: str, columns: list[str], widths: dict[str, int], sep: str) -> list[str]:
    """Slice row into per-column cells using the known widths/separator, asserting the
    separator actually shows up where expected along the way."""
    cells = []
    pos = 0
    for i, key in enumerate(columns):
        w = widths[key]
        cells.append(row[pos : pos + w])
        pos += w
        if i != len(columns) - 1:
            assert row[pos : pos + len(sep)] == sep
            pos += len(sep)
    assert pos == len(row)
    return cells


# --------------------------------------------------------------------------- sanitize_cell


class TestSanitizeCell:
    def test_pads_short_value_left(self):
        assert sanitize_cell("AB", 5) == "AB   "

    def test_pads_short_value_right(self):
        assert sanitize_cell("AB", 5, align="right") == "   AB"

    def test_truncates_long_value(self):
        result = sanitize_cell("ABCDEFGH", 4)
        assert result == "ABCD"
        assert len(result) == 4

    def test_never_wider_than_width(self):
        for width in range(0, 12):
            result = sanitize_cell("this is a fairly long cell value", width)
            assert len(result) == width

    def test_strips_control_characters(self):
        result = sanitize_cell("AB\x00CD\x01EF", 20)
        assert "\x00" not in result
        assert "\x01" not in result
        assert len(result) == 20

    def test_none_value_is_blank_cell(self):
        assert sanitize_cell(None, 4) == "    "

    def test_non_string_value_is_blank_cell(self):
        assert sanitize_cell(12345, 4) == "    "

    def test_zero_width_is_empty(self):
        assert sanitize_cell("anything", 0) == ""


# --------------------------------------------------------------------------- layout()


class TestLayout:
    def test_all_columns_fit_when_width_is_generous(self):
        result = layout(DEFAULT_COLUMNS, available_width=500, owner_width=30)
        assert result == DEFAULT_COLUMNS

    def test_flight_always_kept_even_at_tiny_width(self):
        result = layout(DEFAULT_COLUMNS, available_width=1, owner_width=30)
        assert result == ["flight"]

    def test_flight_kept_even_when_zero_width(self):
        result = layout(DEFAULT_COLUMNS, available_width=0, owner_width=30)
        assert result == ["flight"]

    def test_columns_drop_progressively_as_width_shrinks(self):
        prev_len = len(DEFAULT_COLUMNS) + 1
        # Sweep from generous to tiny; the surviving column count must never increase as
        # available_width shrinks, and "flight" must always remain.
        for width in range(200, 0, -5):
            result = layout(DEFAULT_COLUMNS, available_width=width, owner_width=30)
            assert "flight" in result
            assert len(result) <= prev_len
            prev_len = len(result)
        assert prev_len == 1

    def test_lowest_priority_columns_drop_before_higher_priority_ones(self):
        # "hex" (priority 13) and "cpa" (priority 12) are lower priority than "alt"
        # (priority 1) and "gs" (priority 2); at a width that can only fit a handful of
        # columns, the low-priority ones must be gone while the high-priority ones remain.
        columns = ["flight", "alt", "gs", "cpa", "hex"]
        # widths: flight 7, alt 7, gs 6, cpa 8, hex 6 -> min sum 34 + 4 seps = 38
        result = layout(columns, available_width=25, owner_width=30)
        assert "flight" in result
        assert "alt" in result
        assert "hex" not in result
        assert "cpa" not in result

    def test_unknown_columns_are_ignored(self):
        result = layout(["flight", "bogus_column"], available_width=500, owner_width=30)
        assert result == ["flight"]

    def test_never_drops_below_one_column(self):
        for width in (0, 1, 2, 3):
            result = layout(["flight"], available_width=width, owner_width=30)
            assert result == ["flight"]


# --------------------------------------------------------------------------- compute_widths()


class TestComputeWidths:
    def test_never_exceeds_available_width(self):
        # Sweep from "just enough for the mandatory flight column" up to generous. Below
        # that floor no layout can both keep flight and fit -- see
        # test_below_flight_floor_may_exceed_width for that documented edge case.
        flight_floor = COLUMN_SPECS["flight"].min_width
        for width in range(flight_floor, 150, 7):
            for owner_width in (0, 10, 30, 60):
                columns = layout(DEFAULT_COLUMNS, available_width=width, owner_width=owner_width)
                widths = compute_widths(columns, available_width=width, owner_width=owner_width)
                total = sum(widths.values()) + max(0, len(columns) - 1)
                assert total <= width, (width, owner_width, columns, widths)

    def test_below_flight_floor_may_exceed_width(self):
        # "flight" is never dropped, so when available_width is narrower than even its own
        # min_width, compute_widths cannot honor both constraints -- it still returns
        # flight at its min_width rather than something narrower (and therefore corrupt).
        flight_floor = COLUMN_SPECS["flight"].min_width
        tiny_width = flight_floor - 1
        columns = layout(DEFAULT_COLUMNS, available_width=tiny_width, owner_width=30)
        assert columns == ["flight"]
        widths = compute_widths(columns, available_width=tiny_width, owner_width=30)
        assert widths["flight"] == flight_floor

    def test_every_column_gets_at_least_its_min_width(self):
        columns = ["flight", "alt", "gs"]
        widths = compute_widths(columns, available_width=100, owner_width=30)
        for key in columns:
            assert widths[key] >= COLUMN_SPECS[key].min_width

    def test_no_column_exceeds_its_max_width(self):
        columns = ["flight", "alt", "gs", "owner"]
        widths = compute_widths(columns, available_width=500, owner_width=30)
        for key in columns:
            assert widths[key] <= COLUMN_SPECS[key].max_width

    def test_owner_gets_leftover_space_up_to_owner_width(self):
        columns = ["flight", "owner"]
        # flight min 7 + owner min 5 + 1 sep = 13; give it plenty of room.
        widths = compute_widths(columns, available_width=13 + 20, owner_width=25)
        assert widths["owner"] == 25

    def test_owner_capped_at_its_own_max_width_even_with_huge_owner_width(self):
        columns = ["flight", "owner"]
        widths = compute_widths(columns, available_width=500, owner_width=1000)
        assert widths["owner"] == COLUMN_SPECS["owner"].max_width

    def test_leftover_beyond_owner_cap_goes_to_widest_remaining_column(self):
        columns = ["flight", "gs"]
        # No "owner" column present, so all leftover must go to flight or gs.
        widths = compute_widths(columns, available_width=100, owner_width=30)
        total = sum(widths.values()) + 1
        assert total <= 100
        assert widths["gs"] <= COLUMN_SPECS["gs"].max_width
        assert widths["flight"] <= COLUMN_SPECS["flight"].max_width

    def test_empty_columns_returns_empty_widths(self):
        assert compute_widths([], available_width=100, owner_width=30) == {}

    def test_unknown_columns_are_ignored(self):
        widths = compute_widths(["flight", "nope"], available_width=100, owner_width=30)
        assert "nope" not in widths
        assert "flight" in widths


# --------------------------------------------------------------------------- format_row()


class TestFormatRow:
    def test_cells_are_exactly_the_assigned_widths_for_a_full_aircraft(self):
        ac = full_aircraft()
        columns = layout(DEFAULT_COLUMNS, available_width=120, owner_width=30)
        widths = compute_widths(columns, available_width=120, owner_width=30)
        for ascii_only in (False, True):
            row = format_row(ac, columns, widths, "imperial", ascii_only)
            sep = sep_for(ascii_only)
            cells = split_by_widths(row, columns, widths, sep)
            for key, cell in zip(columns, cells, strict=True):
                assert len(cell) == widths[key]
            assert len(row) == expected_total_len(columns, widths)

    def test_cells_are_exactly_the_assigned_widths_for_a_bare_aircraft(self):
        # An aircraft with virtually nothing populated must still render every column at
        # exactly its assigned width, with sensible blanks (N/A or empty) rather than a
        # crash or a short/overflowing cell.
        ac = make_aircraft()
        columns = layout(DEFAULT_COLUMNS, available_width=120, owner_width=30)
        widths = compute_widths(columns, available_width=120, owner_width=30)
        row = format_row(ac, columns, widths, "imperial", False)
        cells = split_by_widths(row, columns, widths, sep_for(False))
        for key, cell in zip(columns, cells, strict=True):
            assert len(cell) == widths[key]
        # a handful of columns should show a real "no data" marker, not garbage
        by_key = dict(zip(columns, cells, strict=True))
        if "reg" in by_key:
            assert "N/A" in by_key["reg"]
        if "alt" in by_key:
            assert "N/A" in by_key["alt"]
        if "gs" in by_key:
            assert "N/A" in by_key["gs"]

    def test_does_not_raise_for_every_field_none(self):
        # Explicitly None out every optional field a builder might touch.
        ac = make_aircraft(
            flight=None,
            registration=None,
            type_code=None,
            altitude_ft=None,
            baro_rate_fpm=None,
            geom_rate_fpm=None,
            ground_speed_kt=None,
            distance_mi=None,
            bearing_deg=None,
            cpa_distance_mi=None,
            cpa_seconds=None,
            owner_name=None,
            category=None,
            seen_pos_s=None,
        )
        columns = list(COLUMN_SPECS.keys())
        widths = {k: COLUMN_SPECS[k].min_width for k in columns}
        row = format_row(ac, columns, widths, "metric", False)
        assert isinstance(row, str)
        assert len(row) == expected_total_len(columns, widths)

    def test_nul_byte_in_flight_does_not_raise_or_corrupt_length(self):
        ac = make_aircraft(flight="AB\x00CD\x01EF\x1b[31m")
        columns = ["flight", "alt", "gs"]
        widths = {"flight": 8, "alt": 7, "gs": 6}
        row = format_row(ac, columns, widths, "imperial", False)
        assert "\x00" not in row
        assert "\x01" not in row
        assert "\x1b" not in row
        assert len(row) == expected_total_len(columns, widths)

    def test_control_characters_do_not_widen_the_flight_cell(self):
        ac = make_aircraft(flight="X" * 3 + "\x00" * 20)
        columns = ["flight"]
        widths = {"flight": 8}
        row = format_row(ac, columns, widths, "imperial", False)
        assert len(row) == 8

    def test_unknown_unit_system_does_not_raise(self):
        # unit_system is normally validated by config.py, but format_row must still not
        # blow up the whole row if it ever sees a bad value -- degrade cells to blank.
        ac = full_aircraft()
        columns = ["flight", "alt", "gs"]
        widths = {"flight": 8, "alt": 7, "gs": 6}
        row = format_row(ac, columns, widths, "bogus_units", False)
        assert len(row) == expected_total_len(columns, widths)

    def test_selected_marks_first_column_without_changing_width(self):
        ac = full_aircraft()
        columns = ["flight", "alt"]
        widths = {"flight": 8, "alt": 7}
        plain = format_row(ac, columns, widths, "imperial", False, selected=False)
        marked = format_row(ac, columns, widths, "imperial", False, selected=True)
        assert len(plain) == len(marked)
        assert marked.startswith(">")
        assert marked[1:] == plain[1:]

    def test_vs_blank_when_no_rate_data(self):
        ac = make_aircraft(baro_rate_fpm=None, geom_rate_fpm=None)
        columns = ["vs"]
        widths = {"vs": COLUMN_SPECS["vs"].min_width}
        row = format_row(ac, columns, widths, "imperial", False)
        assert row.strip() == ""
        assert len(row) == widths["vs"]

    def test_flags_column_includes_military_and_category(self):
        ac = make_aircraft(db_flags=0b0001, category="A5")
        columns = ["flags"]
        widths = {"flags": COLUMN_SPECS["flags"].max_width}
        row = format_row(ac, columns, widths, "imperial", False)
        assert "MIL" in row
        assert "Heavy" in row

    def test_alert_column_short_tag(self):
        ac = make_aircraft(alert_level=AlertLevel.EMERGENCY)
        columns = ["alert"]
        widths = {"alert": COLUMN_SPECS["alert"].max_width}
        row = format_row(ac, columns, widths, "imperial", False)
        assert "EMERG" in row


# --------------------------------------------------------------------------- format_header()


class TestFormatHeader:
    def test_header_widths_match_widths_dict(self):
        columns = layout(DEFAULT_COLUMNS, available_width=120, owner_width=30)
        widths = compute_widths(columns, available_width=120, owner_width=30)
        header = format_header(columns, widths, "unicode")
        cells = split_by_widths(header, columns, widths, sep_for(False))
        for key, cell in zip(columns, cells, strict=True):
            assert len(cell) == widths[key]

    def test_unknown_border_style_raises(self):
        import pytest

        with pytest.raises(ValueError, match="unknown border style"):
            format_header(["flight"], {"flight": 8}, "not-a-style")

    def test_none_border_style_uses_single_space_separator(self):
        columns = ["flight", "alt"]
        widths = {"flight": 8, "alt": 7}
        header = format_header(columns, widths, "none")
        assert len(header) == expected_total_len(columns, widths)
        assert header[8] == " "

    def test_header_and_row_columns_align_unicode(self):
        ac = full_aircraft()
        columns = layout(DEFAULT_COLUMNS, available_width=120, owner_width=30)
        widths = compute_widths(columns, available_width=120, owner_width=30)
        header = format_header(columns, widths, "unicode")
        row = format_row(ac, columns, widths, "imperial", ascii_only=False)
        assert len(header) == len(row)
        sep = sep_for(False)
        # Slicing both lines by the same columns/widths/separator must succeed for both,
        # which proves the separator (and therefore every column boundary) lands at the
        # same position in the header as in the row.
        header_cells = split_by_widths(header, columns, widths, sep)
        row_cells = split_by_widths(row, columns, widths, sep)
        assert len(header_cells) == len(row_cells) == len(columns)

    def test_header_and_row_columns_align_ascii(self):
        ac = full_aircraft()
        columns = layout(DEFAULT_COLUMNS, available_width=120, owner_width=30)
        widths = compute_widths(columns, available_width=120, owner_width=30)
        header = format_header(columns, widths, "ascii")
        row = format_row(ac, columns, widths, "imperial", ascii_only=True)
        assert len(header) == len(row)
        sep = sep_for(True)
        header_cells = split_by_widths(header, columns, widths, sep)
        row_cells = split_by_widths(row, columns, widths, sep)
        assert len(header_cells) == len(row_cells) == len(columns)
