"""Tests for adsbtui.enrich.providers: per-field resolution across the provider stack."""

from __future__ import annotations

import gzip

import pytest

from adsbtui.enrich import (
    AircraftInfo,
    CsvProvider,
    DerivedProvider,
    Enricher,
    FeedProvider,
    SqliteProvider,
    merge,
)
from adsbtui.enrich.registry_update import build_from_tar1090
from adsbtui.model import Aircraft

# A004B3 is N100 by the derived algorithm too, so it is the hex to use when a test needs
# the database and the derived provider to disagree about nothing but the extra fields.
DB_SAMPLE = (
    "A004B3;N100;J3;00;PIPER J-3 Cub;1940;BENE MARY D;\n"
    "3C6444;D-AIBD;A319;00;AIRBUS A-319;;;\n"
    "AE1234;;C130;01;LOCKHEED C-130;1988;US AIR FORCE;\n"
)


@pytest.fixture
def registry_db(tmp_path):
    source = tmp_path / "aircraft.csv.gz"
    with gzip.open(source, "wt", encoding="utf-8") as handle:
        handle.write(DB_SAMPLE)
    db_path = str(tmp_path / "registry.db")
    build_from_tar1090(str(source), db_path)
    return db_path


@pytest.fixture
def owner_csv(tmp_path):
    path = tmp_path / "master.csv"
    path.write_text(
        "N-NUMBER,NAME,MODE S CODE HEX\n100,LEGACY CSV OWNER,A004B3\n500,CSV ONLY OWNER,A00500\n",
        encoding="utf-8",
    )
    return str(path)


def aircraft(hex_id="a004b3", **kwargs):
    return Aircraft(hex=hex_id, is_icao=not hex_id.startswith("~"), **kwargs)


class TestProvidersInIsolation:
    def test_feed_provider_reads_the_record(self):
        info = FeedProvider().lookup(
            "a004b3",
            aircraft(registration="N999XY", type_code="C172", owner_operator="FEED OWNER"),
        )
        assert info.registration == "N999XY"
        assert info.type_code == "C172"
        assert info.owner == "FEED OWNER"

    def test_feed_provider_with_nothing_to_say(self):
        assert FeedProvider().lookup("a004b3", aircraft()) is None
        assert FeedProvider().lookup("a004b3", None) is None

    def test_feed_db_flags_only_count_when_set(self):
        assert FeedProvider().lookup("a004b3", aircraft(db_flags=0)) is None
        info = FeedProvider().lookup("a004b3", aircraft(db_flags=0x3))
        assert info.db_flags == 0x3
        assert info.is_military is True

    def test_sqlite_provider(self, registry_db):
        info = SqliteProvider(registry_db).lookup("A004B3")
        assert info.registration == "N100"
        assert info.type_code == "J3"
        assert info.type_desc == "PIPER J-3 Cub"
        assert info.owner == "BENE MARY D"
        assert info.year == "1940"

    def test_sqlite_provider_military_flag(self, registry_db):
        info = SqliteProvider(registry_db).lookup("ae1234")
        assert info.db_flags == 0x1
        assert info.is_military is True

    def test_sqlite_provider_never_raises(self, tmp_path, registry_db):
        assert SqliteProvider(str(tmp_path / "missing.db")).lookup("a004b3") is None
        assert SqliteProvider(registry_db).lookup("abcdef") is None
        assert SqliteProvider(registry_db).lookup("~a004b3") is None

        junk = tmp_path / "junk.db"
        junk.write_bytes(b"definitely not sqlite")
        assert SqliteProvider(str(junk)).lookup("a004b3") is None

    def test_csv_provider(self, owner_csv):
        provider = CsvProvider(owner_csv)
        assert provider.lookup("a004b3").owner == "LEGACY CSV OWNER"
        assert provider.lookup("A00500").owner == "CSV ONLY OWNER"
        assert provider.lookup("3c6444") is None

    def test_csv_provider_survives_a_missing_file(self, tmp_path):
        assert CsvProvider(str(tmp_path / "gone.csv")).lookup("a004b3") is None

    def test_derived_provider(self):
        info = DerivedProvider().lookup("A004B3")
        assert info.registration == "N100"
        assert info.country == "United States"
        assert info.country_iso2 == "US"
        assert info.is_military is None

        assert DerivedProvider().lookup("AE1234").is_military is True
        assert DerivedProvider().lookup("200000") is None


class TestMerge:
    def test_first_non_none_wins_each_field(self):
        result = merge(
            [
                ("feed", AircraftInfo(registration="N999XY")),
                ("db", AircraftInfo(registration="N100", owner="DB OWNER")),
                ("derived", AircraftInfo(registration="N100", country="United States")),
            ]
        )
        assert result.registration == "N999XY"
        assert result.owner == "DB OWNER"
        assert result.country == "United States"
        assert result.sources == {
            "registration": "feed",
            "owner": "db",
            "country": "derived",
        }

    def test_empty_merge(self):
        assert merge([("db", None)]) == AircraftInfo()


class TestEnricherPriority:
    def test_feed_beats_the_database(self, registry_db):
        enricher = Enricher.from_paths(db_path=registry_db)
        info = enricher.enrich(aircraft(registration="N999XY"))

        assert info.registration == "N999XY"
        assert info.sources["registration"] == "feed"
        # ...but the database still supplies everything the feed did not have.
        assert info.owner == "BENE MARY D"
        assert info.sources["owner"] == "db"

    def test_database_beats_derived(self, registry_db):
        enricher = Enricher.from_paths(db_path=registry_db)
        info = enricher.lookup("3C6444")

        assert info.registration == "D-AIBD"
        assert info.sources["registration"] == "db"
        assert info.country == "Germany"
        assert info.sources["country"] == "derived"

    def test_database_beats_the_legacy_csv(self, registry_db, owner_csv):
        enricher = Enricher.from_paths(db_path=registry_db, csv_path=owner_csv)
        info = enricher.lookup("a004b3")

        assert info.owner == "BENE MARY D"
        assert info.sources["owner"] == "db"

    def test_csv_fills_in_what_the_database_lacks(self, registry_db, owner_csv):
        enricher = Enricher.from_paths(db_path=registry_db, csv_path=owner_csv)
        info = enricher.lookup("A00500")

        assert info.owner == "CSV ONLY OWNER"
        assert info.sources["owner"] == "csv"
        assert info.registration == "N100CE"  # nothing but the address knew this one
        assert info.sources["registration"] == "derived"

    def test_order_is_configurable(self, registry_db):
        # A user whose receiver has a stale db-file can put the downloaded registry first.
        enricher = Enricher.from_paths(db_path=registry_db, order=("db", "feed", "derived"))
        info = enricher.enrich(aircraft(registration="N999XY"))

        assert info.registration == "N100"
        assert info.sources["registration"] == "db"
        assert info.country == "United States"
        assert info.sources["country"] == "derived"

    def test_unknown_provider_name_is_rejected(self):
        with pytest.raises(ValueError, match="nonesuch"):
            Enricher.from_paths(order=("feed", "nonesuch"))


class TestEnricherWithoutData:
    def test_no_registry_at_all_still_derives(self):
        enricher = Enricher.from_paths()
        info = enricher.lookup("A004B3")

        assert info.registration == "N100"
        assert info.country == "United States"
        assert info.country_iso2 == "US"
        assert info.owner is None
        assert info.sources["registration"] == "derived"

    def test_missing_database_path_is_not_an_error(self, tmp_path):
        enricher = Enricher.from_paths(db_path=str(tmp_path / "never-built.db"))
        info = enricher.lookup("A004B3")

        assert info.registration == "N100"
        assert info.country == "United States"

    def test_nothing_known_anywhere(self):
        info = Enricher.from_paths().lookup("200000")
        assert info == AircraftInfo()

    def test_non_icao_address_falls_back_to_the_feed(self):
        enricher = Enricher.from_paths()
        info = enricher.enrich(aircraft("~abc123", registration="N42"))

        assert info.registration == "N42"
        assert info.country is None


class TestEnricherCaching:
    class CountingProvider:
        name = "db"
        per_aircraft = False

        def __init__(self):
            self.calls = 0

        def lookup(self, hex_id, aircraft=None):
            self.calls += 1
            return AircraftInfo(owner=f"OWNER {hex_id}")

    def test_address_only_providers_are_consulted_once_per_hex(self):
        counter = self.CountingProvider()
        enricher = Enricher([counter, DerivedProvider()])

        for _ in range(5):
            assert enricher.lookup("A004B3").owner == "OWNER a004b3"
        enricher.lookup("3C6444")

        assert counter.calls == 2

    def test_clear_cache_forces_a_re_read(self):
        counter = self.CountingProvider()
        enricher = Enricher([counter])

        enricher.lookup("A004B3")
        enricher.clear_cache()
        enricher.lookup("A004B3")

        assert counter.calls == 2

    def test_the_feed_is_never_cached(self):
        enricher = Enricher([FeedProvider(), DerivedProvider()])

        first = enricher.enrich(aircraft(registration="N111AA"))
        second = enricher.enrich(aircraft(registration="N222BB"))

        assert first.registration == "N111AA"
        assert second.registration == "N222BB"

    def test_cache_is_bounded(self):
        counter = self.CountingProvider()
        enricher = Enricher([counter], cache_size=2)

        for value in range(0xA00001, 0xA00006):
            enricher.lookup(f"{value:06x}")

        assert len(enricher._cache) <= 2


class TestApply:
    def test_fills_empty_fields_only(self, registry_db):
        enricher = Enricher.from_paths(db_path=registry_db)
        record = aircraft(registration="N999XY")

        info = enricher.apply(record)

        assert record.registration == "N999XY"  # the feed's value survives
        assert record.type_code == "J3"
        assert record.type_desc == "PIPER J-3 Cub"
        assert record.owner_operator == "BENE MARY D"
        assert record.owner_name == "BENE MARY D"
        assert record.year == "1940"
        assert info.registration == "N999XY"

    def test_sets_the_military_flag_from_the_database(self, registry_db):
        enricher = Enricher.from_paths(db_path=registry_db)
        record = aircraft("ae1234")

        enricher.apply(record)

        assert record.db_flags == 0x1
        assert record.is_military is True

    def test_sets_the_military_flag_from_the_address_alone(self):
        enricher = Enricher.from_paths()
        record = aircraft("adf7c8")

        enricher.apply(record)

        assert record.is_military is True

    def test_derived_registration_reaches_the_display_name(self):
        enricher = Enricher.from_paths()
        record = aircraft("a004b3")

        enricher.apply(record)

        assert record.registration == "N100"
        assert record.display_flight == "N100"

    def test_leaves_a_record_alone_when_nothing_is_known(self):
        record = aircraft("200000")
        Enricher.from_paths().apply(record)

        assert record.registration is None
        assert record.owner_name is None
        assert record.db_flags == 0
