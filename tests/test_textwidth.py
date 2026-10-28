"""Tests for adsbtui.textwidth and the bearing rounding fix (A09)."""

from __future__ import annotations

import pytest

from adsbtui import textwidth
from adsbtui.model import Aircraft
from adsbtui.ui import columns as columns_mod
from adsbtui.ui.screens.detail import DetailScreen


def test_width_of_ascii_cjk_and_combining():
    assert textwidth.display_width("abc") == 3
    assert textwidth.display_width("日本") == 4
    assert textwidth.display_width("e\u0301") == 1


@pytest.mark.parametrize("width", range(0, 8))
def test_clip_never_exceeds_width(width):
    assert textwidth.display_width(textwidth.clip("日a本b語", width)) <= width


def test_ljust_and_rjust_pad_to_exact_width():
    assert textwidth.ljust("日", 4) == "日  "
    assert textwidth.rjust("日", 4) == "  日"
    assert textwidth.ljust("日本", 3) == "日 "


@pytest.mark.parametrize(
    "bearing,expected",
    [(359.6, "000"), (359.4, "359"), (0.0, "000"), (0.4, "000"), (180.5, "180"), (359.99, "000")],
)
def test_bearing_rounds_then_wraps(bearing, expected):
    ac = Aircraft(hex="abc123", is_icao=True, bearing_deg=bearing)
    assert columns_mod._cell_brg(ac, "imperial", False).endswith(expected)
    detail = "\n".join(DetailScreen(ac, "imperial").render_lines(100, 60))
    assert f" {expected}°" in detail
