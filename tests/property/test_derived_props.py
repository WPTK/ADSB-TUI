"""Property tests for the ICAO address <-> N-number mapping."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from adsbtui.enrich.derived import hex_from_registration, registration_from_hex


@given(st.integers(min_value=0xA00001, max_value=0xADF7C7))
def test_us_hex_round_trips_through_its_n_number(address):
    hex_id = f"{address:06x}"
    reg = registration_from_hex(hex_id)
    assert reg is not None and reg.startswith("N")
    assert hex_from_registration(reg) == hex_id


@given(st.text())
def test_registration_lookups_never_raise(text):
    hex_from_registration(text)
    registration_from_hex(text)
