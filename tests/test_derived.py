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


# ----------------------------------------------------------------------------------
# Ground-truth vectors
# ----------------------------------------------------------------------------------

#: (ICAO hex, N-number) pairs taken from the tar1090 community registry and
#: double-confirmed: the database agrees with the derivation AND the tail number encodes
#: back to the same address. They deliberately cover every shape an N-number can take --
#: bare digits, one trailing letter, two trailing letters, at each digit length -- and both
#: ends of the allocation.
#:
#: These exist because a round-trip test is not enough. The first implementation of the
#: suffix encoder laid all 24 single letters out before all 576 pairs, instead of giving
#: each first letter its own block of 25 (A, AA..AZ, B, BA..). That still produces exactly
#: 601 slots and a perfectly self-consistent inverse, so every round-trip test passed while
#: three out of four real aircraft got the wrong tail number. Only real pairs catch it.
REAL_REGISTRATIONS = [
    ("a00001", "N1"),  # 1 digits, 0 letters
    ("a00002", "N1A"),  # 1 digits, 1 letters
    ("a00003", "N1AA"),  # 1 digits, 2 letters
    ("a00005", "N1AC"),  # 1 digits, 2 letters
    ("a00034", "N1C"),  # 1 digits, 1 letters
    ("a0025b", "N10A"),  # 2 digits, 1 letters
    ("a0025c", "N10AA"),  # 2 digits, 2 letters
    ("a0025d", "N10AB"),  # 2 digits, 2 letters
    ("a00274", "N10B"),  # 2 digits, 1 letters
    ("a004b3", "N100"),  # 3 digits, 0 letters
    ("a004b4", "N100A"),  # 3 digits, 1 letters
    ("a004b5", "N100AA"),  # 3 digits, 2 letters
    ("a004b6", "N100AB"),  # 3 digits, 2 letters
    ("a004cd", "N100B"),  # 3 digits, 1 letters
    ("a0070d", "N1000A"),  # 4 digits, 1 letters
    ("a00725", "N10000"),  # 5 digits, 0 letters
    ("a00726", "N10001"),  # 5 digits, 0 letters
    ("a0072f", "N1001"),  # 4 digits, 0 letters
    ("a00730", "N1001A"),  # 4 digits, 1 letters
    ("a00775", "N1003"),  # 4 digits, 0 letters
    ("a0086a", "N101"),  # 3 digits, 0 letters
    ("a029d9", "N11"),  # 2 digits, 0 letters
    ("a05158", "N12"),  # 2 digits, 0 letters
    ("a18d50", "N2"),  # 1 digits, 0 letters
]


@pytest.mark.parametrize("hex_id,expected", REAL_REGISTRATIONS)
def test_derives_real_registrations(hex_id, expected):
    assert registration_from_hex(hex_id) == expected


@pytest.mark.parametrize("hex_id,expected", REAL_REGISTRATIONS)
def test_encodes_real_registrations_back_to_their_address(hex_id, expected):
    assert hex_from_registration(expected) == hex_id


def test_suffix_blocks_interleave_rather_than_grouping():
    """The sequence runs A, AA, AB ... AZ, B -- not A, B ... Z, AA, AB."""
    got = [registration_from_hex(hex(0xA004B3 + n)[2:]) for n in range(28)]
    assert got[:4] == ["N100", "N100A", "N100AA", "N100AB"]
    assert got[25] == "N100AZ"
    assert got[26] == "N100B"
