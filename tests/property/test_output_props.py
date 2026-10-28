"""Property tests: whatever the feed contained, the output formats are safe to print."""

from __future__ import annotations

import csv
import io
import json

from hypothesis import given
from hypothesis import strategies as st

from adsbtui.normalize import parse_aircraft
from adsbtui.output import render_csv, render_json, render_table
from tests.property.strategies import records_with_hex

aircraft_lists = st.lists(records_with_hex, max_size=4).map(
    lambda records: [ac for r in records if (ac := parse_aircraft(r)) is not None]
)
units_names = st.sampled_from(["imperial", "metric", "aviation"])

#: C0 controls, DEL and C1 controls.
BAD = set(range(0x00, 0x20)) | {0x7F} | set(range(0x80, 0xA0))


def _no_controls(text: str, allow: str = "") -> bool:
    return all(ord(ch) not in BAD or ch in allow for ch in text)


@given(aircraft_lists, units_names)
def test_table_has_no_control_characters(aircraft, units):
    assert _no_controls(render_table(aircraft, units), allow="\n")


@given(aircraft_lists, units_names)
def test_csv_has_no_control_characters_and_parses_back(aircraft, units):
    text = render_csv(aircraft, units)
    assert "\r" not in text
    assert _no_controls(text, allow="\n")
    rows = list(csv.reader(io.StringIO(text)))
    assert len(rows) == len(aircraft) + 1


@given(aircraft_lists, units_names)
def test_json_round_trips_and_is_strict(aircraft, units):
    text = render_json(aircraft, units)
    assert len(json.loads(text, parse_constant=_reject)) == len(aircraft)


def _reject(name):
    raise AssertionError(f"non-standard JSON constant {name}")
