"""Tests for adsbtui.history: sqlite persistence of closed-track sightings and stats."""

from __future__ import annotations

import csv
import sqlite3

from adsbtui.history import (
    Stats,
    ensure_schema,
    export_unknown,
    get_stats,
    prune_old,
    record_close,
)
from adsbtui.model import Aircraft


def _aircraft(hex_id: str, **kwargs) -> Aircraft:
    return Aircraft(hex=hex_id, is_icao=True, **kwargs)


class TestEnsureSchema:
    def test_idempotent(self, tmp_path):
        db_path = str(tmp_path / "sub" / "history.db")
        ensure_schema(db_path)
        ensure_schema(db_path)  # must not raise

        conn = sqlite3.connect(db_path)
        try:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "sightings" in names

    def test_creates_parent_directory(self, tmp_path):
        db_path = str(tmp_path / "does" / "not" / "exist" / "history.db")
        ensure_schema(db_path)
        assert (tmp_path / "does" / "not" / "exist" / "history.db").exists()

    def test_sets_wal_journal_mode(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        ensure_schema(db_path)

        conn = sqlite3.connect(db_path)
        try:
            (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
        finally:
            conn.close()
        assert mode.lower() == "wal"


class TestRecordCloseAndStats:
    def test_get_stats_on_empty_table(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        ensure_schema(db_path)

        stats = get_stats(db_path, since=0.0)

        assert stats == Stats(
            unique_count=0,
            closest=None,
            highest_altitude=None,
            fastest=None,
            emergency_count=0,
        )

    def test_stats_reflect_recorded_values(self, tmp_path):
        db_path = str(tmp_path / "history.db")

        # Aircraft A: closer, lower, slower, no emergency.
        a = _aircraft(
            "aaaaaa",
            flight="UAL123",
            registration="N123AB",
            type_code="B738",
            owner_name="United",
            db_flags=0,
        )
        record_close(
            db_path,
            a,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=3.5,
            min_distance_at=150.0,
            max_altitude_ft=35000.0,
            max_gs_kt=420.0,
            squawks_seen=["1200"],
            had_emergency=False,
        )

        # Aircraft B: farther, higher, faster, and emergency squawked.
        b = _aircraft(
            "bbbbbb",
            flight="SWA456",
            registration=None,
            type_code="A320",
            owner_name=None,
            db_flags=1,
        )
        record_close(
            db_path,
            b,
            first_seen=110.0,
            last_seen=210.0,
            min_distance_mi=8.2,
            min_distance_at=180.0,
            max_altitude_ft=41000.0,
            max_gs_kt=480.0,
            squawks_seen=["7700", "1200"],
            had_emergency=True,
        )

        stats = get_stats(db_path, since=0.0)

        assert stats.unique_count == 2
        assert stats.emergency_count == 1
        assert stats.closest == ("aaaaaa", 3.5, 150.0)
        assert stats.highest_altitude == ("bbbbbb", 41000.0)
        assert stats.fastest == ("bbbbbb", 480.0)

    def test_get_stats_respects_since(self, tmp_path):
        db_path = str(tmp_path / "history.db")

        old = _aircraft("cccccc")
        record_close(
            db_path,
            old,
            first_seen=10.0,
            last_seen=20.0,
            min_distance_mi=1.0,
            min_distance_at=15.0,
            max_altitude_ft=10000.0,
            max_gs_kt=200.0,
            squawks_seen=None,
            had_emergency=False,
        )

        recent = _aircraft("dddddd")
        record_close(
            db_path,
            recent,
            first_seen=1000.0,
            last_seen=1010.0,
            min_distance_mi=5.0,
            min_distance_at=1005.0,
            max_altitude_ft=20000.0,
            max_gs_kt=300.0,
            squawks_seen=None,
            had_emergency=False,
        )

        stats = get_stats(db_path, since=500.0)
        assert stats.unique_count == 1
        assert stats.closest == ("dddddd", 5.0, 1005.0)

    def test_insert_or_replace_on_same_key(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        a = _aircraft("eeeeee")

        record_close(
            db_path,
            a,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=10.0,
            min_distance_at=150.0,
            max_altitude_ft=10000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )
        # Same (hex, first_seen) key -- should replace, not duplicate.
        record_close(
            db_path,
            a,
            first_seen=100.0,
            last_seen=250.0,
            min_distance_mi=2.0,
            min_distance_at=220.0,
            max_altitude_ft=15000.0,
            max_gs_kt=150.0,
            squawks_seen=None,
            had_emergency=True,
        )

        stats = get_stats(db_path, since=0.0)
        assert stats.unique_count == 1
        assert stats.closest == ("eeeeee", 2.0, 220.0)
        assert stats.emergency_count == 1


class TestPruneOld:
    def test_removes_old_rows_keeps_new(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        now = 1_000_000.0
        retention_days = 30

        old = _aircraft("111111")
        record_close(
            db_path,
            old,
            first_seen=0.0,
            last_seen=now - (retention_days + 1) * 86400,
            min_distance_mi=1.0,
            min_distance_at=0.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        new = _aircraft("222222")
        record_close(
            db_path,
            new,
            first_seen=now - 100.0,
            last_seen=now - 50.0,
            min_distance_mi=1.0,
            min_distance_at=0.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        deleted = prune_old(db_path, retention_days=retention_days, now=now)

        assert deleted == 1
        stats = get_stats(db_path, since=0.0)
        assert stats.unique_count == 1

    def test_no_matching_rows_returns_zero(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        ensure_schema(db_path)
        deleted = prune_old(db_path, retention_days=365, now=1_000_000.0)
        assert deleted == 0


class TestExportUnknown:
    def test_exports_only_hexes_without_owner(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        out_path = str(tmp_path / "unknown.csv")

        known = _aircraft("aaaaaa", flight="UAL1", owner_name="United Airlines")
        record_close(
            db_path,
            known,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=1.0,
            min_distance_at=100.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        unknown_null = _aircraft("bbbbbb", flight="N999ZZ", owner_name=None)
        record_close(
            db_path,
            unknown_null,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=1.0,
            min_distance_at=100.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )
        # Second sighting of the same unknown hex, later last_seen.
        record_close(
            db_path,
            unknown_null,
            first_seen=300.0,
            last_seen=400.0,
            min_distance_mi=1.0,
            min_distance_at=300.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        unknown_empty = _aircraft("cccccc", flight="N888ZZ", owner_name="")
        record_close(
            db_path,
            unknown_empty,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=1.0,
            min_distance_at=100.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        count = export_unknown(db_path, out_path)

        assert count == 2

        with open(out_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))

        assert rows[0] == ["hex", "callsign", "count", "last_seen"]
        data_rows = rows[1:]
        assert len(data_rows) == 2

        by_hex = {row[0]: row for row in data_rows}
        assert "aaaaaa" not in by_hex
        assert by_hex["bbbbbb"][1] == "N999ZZ"
        assert by_hex["bbbbbb"][2] == "2"
        assert float(by_hex["bbbbbb"][3]) == 400.0
        assert by_hex["cccccc"][2] == "1"

    def test_no_unknown_rows_writes_header_only(self, tmp_path):
        db_path = str(tmp_path / "history.db")
        out_path = str(tmp_path / "unknown.csv")

        known = _aircraft("dddddd", flight="DAL1", owner_name="Delta")
        record_close(
            db_path,
            known,
            first_seen=100.0,
            last_seen=200.0,
            min_distance_mi=1.0,
            min_distance_at=100.0,
            max_altitude_ft=1000.0,
            max_gs_kt=100.0,
            squawks_seen=None,
            had_emergency=False,
        )

        count = export_unknown(db_path, out_path)
        assert count == 0

        with open(out_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        assert rows == [["hex", "callsign", "count", "last_seen"]]
