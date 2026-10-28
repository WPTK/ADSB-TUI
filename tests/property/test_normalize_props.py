"""Property tests: whatever a feed sends, normalize returns clean data or None."""

from __future__ import annotations

import dataclasses
import math

from hypothesis import given
from hypothesis import strategies as st

from adsbtui.normalize import parse_aircraft, parse_snapshot, sanitize
from tests.property.strategies import hostile_records, json_values, records_with_hex


def _is_clean(text: str) -> bool:
    return all(ch.isprintable() for ch in text)


@given(json_values)
def test_parse_aircraft_never_raises_on_any_json(value):
    parse_aircraft(value)  # type: ignore[arg-type]


@given(hostile_records)
def test_parse_aircraft_never_raises_on_hostile_records(record):
    parse_aircraft(record)


@given(records_with_hex)
def test_every_string_field_is_printable(record):
    ac = parse_aircraft(record)
    assert ac is not None
    for f in dataclasses.fields(ac):
        value = getattr(ac, f.name)
        if isinstance(value, str):
            assert _is_clean(value), (f.name, value)


@given(records_with_hex)
def test_every_number_is_finite_and_in_range(record):
    ac = parse_aircraft(record)
    assert ac is not None
    for f in dataclasses.fields(ac):
        value = getattr(ac, f.name)
        if isinstance(value, float):
            assert math.isfinite(value), (f.name, value)
    if ac.lat is not None:
        assert -90 <= ac.lat <= 90
    if ac.lon is not None:
        assert -180 <= ac.lon <= 180
    assert (ac.lat is None) == (ac.lon is None)
    if ac.ground_speed_kt is not None:
        assert ac.ground_speed_kt >= 0
    for angle in (ac.track_deg, ac.true_heading_deg, ac.mag_heading_deg):
        assert angle is None or 0 <= angle < 360


@given(records_with_hex)
def test_wire_format_fields_match_their_patterns(record):
    ac = parse_aircraft(record)
    assert ac is not None
    assert len(ac.hex.lstrip("~")) == 6
    assert ac.squawk is None or (len(ac.squawk) == 4 and set(ac.squawk) <= set("01234567"))
    assert ac.category is None or (ac.category[0] in "ABCD" and ac.category[1] in "01234567")


@given(json_values)
def test_parse_snapshot_never_raises_on_a_dict(value):
    if isinstance(value, dict):
        snap = parse_snapshot(value, 1.0)
        assert math.isfinite(snap.now)


@given(st.one_of(st.none(), st.integers(), st.floats(), st.text(), st.lists(st.text())))
def test_sanitize_never_raises(value):
    out = sanitize(value, 5)
    assert out is None or (_is_clean(out) and len(out) <= 5)
