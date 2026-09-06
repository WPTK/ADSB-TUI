"""Tests for adsbtui.enrich.derived: the N-number algorithm and the ICAO country table."""

from __future__ import annotations

import pytest

from adsbtui.enrich.derived import (
    N_NUMBER_FIRST_HEX,
    N_NUMBER_LAST_HEX,
    country_for_hex,
    hex_from_registration,
    is_military_hex,
    normalize_hex,
    registration_from_hex,
)


class TestNormalizeHex:
    def test_pads_and_lowercases(self):
        assert normalize_hex("A004B3") == "a004b3"
        assert normalize_hex(" a004b3 ") == "a004b3"
        assert normalize_hex("4b3") == "0004b3"

    def test_rejects_non_icao_and_junk(self):
        assert normalize_hex("~a004b3") is None
        assert normalize_hex("") is None
        assert normalize_hex("zzzzzz") is None
        assert normalize_hex("1000000") is None  # more than 24 bits
        assert normalize_hex(None) is None


class TestRegistrationFromHex:
    def test_known_pairs(self):
        # A004B3 -> N100 is straight out of the tar1090-db file itself.
        assert registration_from_hex("A004B3") == "N100"
        assert registration_from_hex("a004b3") == "N100"

    def test_range_boundaries(self):
        assert registration_from_hex("A00001") == "N1"
        assert registration_from_hex("ADF7C7") == "N99999"
        assert registration_from_hex("A00000") is None
        assert registration_from_hex("ADF7C8") is None

    def test_outside_us_civil_range(self):
        assert registration_from_hex("3C6444") is None  # a German address
        assert registration_from_hex("C01234") is None  # Canadian
        assert registration_from_hex("AE1234") is None  # US military block

    def test_rejects_malformed_input(self):
        assert registration_from_hex("~A004B3") is None
        assert registration_from_hex("nonsense") is None
        assert registration_from_hex("") is None
        assert registration_from_hex(None) is None

    def test_every_shape_is_reachable(self):
        """Each of the legal N-number shapes must decode back to itself."""
        for registration in ("N1", "N1A", "N1AA", "N12", "N123AA", "N1234", "N1234Z", "N99999"):
            address = hex_from_registration(registration)
            assert address is not None, registration
            assert registration_from_hex(address) == registration

    def test_suffix_never_uses_i_or_o(self):
        # 601 suffix slots per digit position, so this window covers every one of them.
        start = N_NUMBER_FIRST_HEX
        suffixes = {
            registration_from_hex(f"{value:06x}")[2:] for value in range(start, start + 601)
        }
        assert len(suffixes) == 601
        assert not any("I" in suffix or "O" in suffix for suffix in suffixes)


class TestRoundTrip:
    def test_round_trip_across_the_range(self):
        """encode(decode(hex)) == hex for a stride over the entire valid range.

        A stride rather than all 915,399 addresses purely for test runtime; the stride is
        coprime with every block size in the encoding, so it lands inside all of them.
        """
        for value in range(N_NUMBER_FIRST_HEX, N_NUMBER_LAST_HEX + 1, 97):
            address = f"{value:06x}"
            registration = registration_from_hex(address)
            assert registration is not None, address
            assert hex_from_registration(registration) == address

    def test_round_trip_at_block_edges(self):
        """Dense coverage where the positional encoding rolls over between blocks."""
        edges = (
            N_NUMBER_FIRST_HEX,
            N_NUMBER_FIRST_HEX + 601,  # first digit's suffixes give way to a second digit
            N_NUMBER_FIRST_HEX + 601 + 10111,
            N_NUMBER_FIRST_HEX + 101711,  # N2
            N_NUMBER_LAST_HEX - 40,
        )
        for edge in edges:
            low = max(edge - 40, N_NUMBER_FIRST_HEX)
            high = min(edge + 41, N_NUMBER_LAST_HEX + 1)
            for value in range(low, high):
                address = f"{value:06x}"
                assert hex_from_registration(registration_from_hex(address)) == address

    def test_registrations_are_unique(self):
        sample = [
            registration_from_hex(f"{value:06x}")
            for value in range(N_NUMBER_FIRST_HEX, N_NUMBER_FIRST_HEX + 5000)
        ]
        assert len(set(sample)) == len(sample)


class TestHexFromRegistration:
    def test_rejects_illegal_registrations(self):
        for bad in (
            "N0",  # no leading zero
            "N1I",  # I is not in the alphabet
            "N1O",  # nor is O
            "N123456",  # too long
            "N12345A",  # five digits leave no room for a suffix
            "N1234AA",  # nor do four digits leave room for two letters
            "N1AAA",  # three letters is not a thing
            "G-ABCD",  # not a US registration at all
            "N",
            "",
            None,
        ):
            assert hex_from_registration(bad) is None, bad

    def test_accepts_lowercase_and_whitespace(self):
        assert hex_from_registration(" n100 ") == "a004b3"


class TestCountryForHex:
    @pytest.mark.parametrize(
        ("address", "expected"),
        [
            ("A004B3", ("US", "United States")),
            ("3C6444", ("DE", "Germany")),
            ("4B1801", ("CH", "Switzerland")),
            ("406B0A", ("GB", "United Kingdom")),
            ("750548", ("MY", "Malaysia")),
            ("C01234", ("CA", "Canada")),
            ("7C1234", ("AU", "Australia")),
        ],
    )
    def test_known_blocks(self, address, expected):
        assert country_for_hex(address) == expected

    def test_unallocated_and_malformed_are_none(self):
        assert country_for_hex("200000") is None  # inside no block we claim to know
        assert country_for_hex("~A004B3") is None
        assert country_for_hex("zzz") is None
        assert country_for_hex("") is None


class TestMilitaryRanges:
    def test_us_military_block(self):
        assert is_military_hex("AE1234") is True
        assert is_military_hex("ADF7C8") is True
        assert is_military_hex("ADF7C7") is False
        assert is_military_hex("A004B3") is False

    def test_military_block_keeps_its_country(self):
        assert country_for_hex("AE1234") == ("US", "United States")

    def test_nested_block_resolves_to_the_most_specific_range(self):
        # 43C000-43CFFF is UK military inside the wider 400000-43FFFF UK allocation.
        assert is_military_hex("43C123") is True
        assert is_military_hex("43D123") is False
        assert country_for_hex("43D123") == ("GB", "United Kingdom")

    def test_unknown_address_is_not_military(self):
        assert is_military_hex("200000") is False
        assert is_military_hex("~AE1234") is False


class TestRangeTable:
    def test_ranges_do_not_partially_overlap(self):
        """Any two ranges must be disjoint or fully nested -- a partial overlap is a typo."""
        from adsbtui.enrich.derived import _table

        ranges, _, _ = _table()
        assert ranges, "the bundled icao_ranges.csv failed to load"
        for outer_index, outer in enumerate(ranges):
            for inner in ranges[outer_index + 1 :]:
                if inner.start > outer.end:
                    break
                assert inner.end <= outer.end, f"{outer} partially overlaps {inner}"

    def test_every_row_is_well_formed(self):
        from adsbtui.enrich.derived import _table

        ranges, _, _ = _table()
        for row in ranges:
            assert row.start <= row.end
            assert len(row.iso2) == 2 and row.iso2.isalpha()
            assert row.country
