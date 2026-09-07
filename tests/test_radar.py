"""Tests for the PPI scope's geometry and styling.

These matter more than they look: a scope that is mirrored, or stretched into an egg, is
worse than no scope at all because it is confidently wrong. Everything here is checked
against the character grid the module returns, with no terminal involved.
"""

from __future__ import annotations

import pytest

from adsbtui.model import Aircraft, AlertLevel
from adsbtui.ui import radar


def _ac(hex_id="abc123", distance_mi=5.0, bearing_deg=0.0, **kw) -> Aircraft:
    ac = Aircraft(hex=hex_id, is_icao=True, **kw)
    ac.distance_mi = distance_mi
    ac.bearing_deg = bearing_deg
    return ac


def _find(scope: radar.Scope, char: str) -> list[tuple[int, int]]:
    """Every (row, col) holding char."""
    return [(r, c) for r, line in enumerate(scope.lines) for c, ch in enumerate(line) if ch == char]


class TestArrows:
    @pytest.mark.parametrize(
        ("track", "expected"),
        [
            (0, "↑"),
            (45, "↗"),
            (90, "→"),
            (135, "↘"),
            (180, "↓"),
            (225, "↙"),
            (270, "←"),
            (315, "↖"),
        ],
    )
    def test_each_octant_gets_its_arrow(self, track, expected):
        assert radar.arrow_for_track(track) == expected

    def test_headings_round_to_the_nearest_octant(self):
        assert radar.arrow_for_track(20) == "↑"  # nearer 0 than 45
        assert radar.arrow_for_track(30) == "↗"  # nearer 45 than 0

    def test_wraps_past_360(self):
        assert radar.arrow_for_track(359) == "↑"
        assert radar.arrow_for_track(360) == "↑"
        assert radar.arrow_for_track(720 + 90) == "→"

    def test_unknown_track_is_a_dot_not_an_arrow(self):
        # An arrow would claim a heading the feed never sent.
        assert radar.arrow_for_track(None) == "•"
        assert radar.arrow_for_track(None, ascii_only=True) == "o"

    def test_ascii_set_is_used_when_asked(self):
        assert radar.arrow_for_track(0, ascii_only=True) == "^"
        assert radar.arrow_for_track(180, ascii_only=True) == "v"


class TestGeometry:
    def test_north_is_up_and_east_is_right(self):
        """The one thing a scope must not get wrong. Bearing 0 has to plot above home and
        bearing 90 to its right; a sign flip here mirrors the whole display."""
        scope = radar.render(
            [_ac("n", distance_mi=10, bearing_deg=0), _ac("e", distance_mi=10, bearing_deg=90)],
            width=41,
            height=21,
            range_mi=10.0,
        )
        home = _find(scope, "⌂")[0]
        north = _find(scope, "•")  # both have no track, so both render as dots
        assert len(north) == 2
        above = [pos for pos in north if pos[0] < home[0]]
        right = [pos for pos in north if pos[1] > home[1]]
        assert len(above) == 1, "bearing 0 must plot above home"
        assert len(right) == 1, "bearing 90 must plot right of home"
        # And the northern one must be vertically, not horizontally, displaced.
        assert above[0][1] == pytest.approx(home[1], abs=1)

    def test_compass_ticks_sit_on_their_bearings(self):
        scope = radar.render([], width=41, height=21, range_mi=10.0)
        home = _find(scope, "⌂")[0]
        n, e, s, w = (_find(scope, label)[0] for label in "NESW")
        assert n[0] < home[0] and s[0] > home[0]
        assert e[1] > home[1] and w[1] < home[1]

    def test_rings_are_circular_not_stretched(self):
        """Cells are twice as tall as wide, so a ring's half-width in columns must be about
        twice its half-height in rows. Without the aspect correction it would be 1:1 and
        the scope would look like an egg."""
        scope = radar.render([], width=61, height=31, range_mi=10.0)
        ring_cells = _find(scope, "·")
        rows = [r for r, _ in ring_cells]
        cols = [c for _, c in ring_cells]
        half_height = (max(rows) - min(rows)) / 2
        half_width = (max(cols) - min(cols)) / 2
        assert half_width / half_height == pytest.approx(1 / radar.ASPECT, rel=0.15)

    def test_distance_scales_to_the_range(self):
        near = radar.render([_ac(distance_mi=1)], width=41, height=21, range_mi=10.0)
        far = radar.render([_ac(distance_mi=9)], width=41, height=21, range_mi=10.0)
        home = _find(near, "⌂")[0]
        near_pos = _find(near, "•")[0]
        far_pos = _find(far, "•")[0]
        assert abs(near_pos[0] - home[0]) < abs(far_pos[0] - home[0])


class TestWhatIsPlotted:
    def test_aircraft_beyond_the_range_are_not_drawn(self):
        # Clamping a stray onto the rim would invent a position it is not at.
        scope = radar.render([_ac(distance_mi=99)], width=41, height=21, range_mi=10.0)
        assert _find(scope, "•") == []

    def test_aircraft_without_a_position_are_not_drawn(self):
        ac = Aircraft(hex="nopos", is_icao=True)
        scope = radar.render([ac], width=41, height=21, range_mi=10.0)
        assert _find(scope, "•") == []

    def test_an_emergency_is_not_overwritten_by_a_later_contact(self):
        """Two aircraft in one cell: the one that matters has to survive."""
        emergency = _ac("emerg", distance_mi=5, bearing_deg=45)
        emergency.alert_level = AlertLevel.EMERGENCY
        ordinary = _ac("plain", distance_mi=5, bearing_deg=45)
        scope = radar.render([emergency, ordinary], width=41, height=21, range_mi=10.0)
        styles = {s for row in scope.styles for s in row}
        assert "emergency" in styles

    def test_selected_aircraft_is_styled_as_selected(self):
        scope = radar.render(
            [_ac("sel", distance_mi=5, bearing_deg=180)],
            width=41,
            height=21,
            range_mi=10.0,
            selected_hex="sel",
        )
        assert "selected" in {s for row in scope.styles for s in row}

    def test_military_and_watchlist_get_their_own_styles(self):
        mil = _ac("mil", distance_mi=4, bearing_deg=10, db_flags=0x1)
        watched = _ac("watch", distance_mi=6, bearing_deg=200)
        watched.is_watched = True
        scope = radar.render([mil, watched], width=41, height=21, range_mi=10.0)
        styles = {s for row in scope.styles for s in row}
        assert {"military", "watchlist"} <= styles


class TestDegenerateSizes:
    @pytest.mark.parametrize(("w", "h"), [(0, 0), (4, 10), (40, 2), (6, 3)])
    def test_a_pane_too_small_to_draw_returns_nothing(self, w, h):
        # The drawing layer skips an empty Scope; it must never be handed a half-circle.
        scope = radar.render([], width=w, height=h, range_mi=10.0)
        assert scope.lines == []

    def test_every_line_is_exactly_the_requested_width(self):
        scope = radar.render([_ac()], width=37, height=19, range_mi=10.0)
        assert {len(line) for line in scope.lines} == {37}
        assert all(len(row) == 37 for row in scope.styles)

    def test_zero_range_plots_nothing_rather_than_dividing_by_zero(self):
        scope = radar.render([_ac(distance_mi=1)], width=41, height=21, range_mi=0.0)
        assert _find(scope, "•") == []


class TestRangeLabel:
    def test_whole_miles_have_no_decimal(self):
        assert radar.range_label(15.0) == "range 15 mi"

    def test_short_ranges_keep_a_decimal(self):
        assert radar.range_label(2.5) == "range 2.5 mi"

    def test_unit_label_is_used(self):
        assert radar.range_label(20.0, "km") == "range 20 km"
