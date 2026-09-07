"""Tests for deciding when the aircraft registry should refresh itself.

The point of this feature is that owner and type data must never depend on the user
knowing to go and fetch it, so "missing" and "stale" both have to count as due, and
"switched off" has to be honoured absolutely.
"""

from __future__ import annotations

import sqlite3

import pytest

from adsbtui.config import Config
from adsbtui.enrich import registry_update
from adsbtui.output import registry_is_due


def _cfg(db_path: str, **registry) -> Config:
    cfg = Config()
    cfg.source.url = "tests/fixtures/aircraft.json"
    cfg.home.lat, cfg.home.lon = 37.7749, -122.4194
    cfg.registry.db = db_path
    for key, value in registry.items():
        setattr(cfg.registry, key, value)
    return cfg


def _db_with_meta(path: str, fetched_at: float) -> None:
    """A registry database whose tar1090 rows were recorded as fetched at a given time.

    Written with plain SQL against the real schema rather than through the builder: the
    builder downloads, and a staleness test has no business touching the network.
    """
    conn = sqlite3.connect(path)
    try:
        conn.executescript(registry_update._SCHEMA)  # noqa: SLF001 - the real schema
        conn.execute(
            "INSERT OR REPLACE INTO meta (source, url, fetched_at, row_count) VALUES (?, ?, ?, ?)",
            (registry_update.SOURCE_TAR1090, registry_update.TAR1090_URL, fetched_at, 0),
        )
        conn.commit()
    finally:
        conn.close()


class TestWhenARefreshIsDue:
    def test_a_missing_database_is_due(self, tmp_path):
        # The case that matters most: a fresh install has no database at all, and the
        # whole feature is pointless if that state persists until someone intervenes.
        assert registry_is_due(_cfg(str(tmp_path / "absent.sqlite"))) is True

    def test_auto_update_off_is_never_due(self, tmp_path):
        cfg = _cfg(str(tmp_path / "absent.sqlite"), auto_update=False)
        assert registry_is_due(cfg) is False

    def test_an_empty_db_path_is_never_due(self):
        assert registry_is_due(_cfg("")) is False

    def test_a_fresh_database_is_not_due(self, tmp_path):
        import time

        path = str(tmp_path / "reg.sqlite")
        _db_with_meta(path, fetched_at=time.time())
        assert registry_is_due(_cfg(path, max_age_days=1)) is False

    def test_a_database_older_than_the_limit_is_due(self, tmp_path):
        import time

        path = str(tmp_path / "reg.sqlite")
        _db_with_meta(path, fetched_at=time.time() - 2 * 86400)
        assert registry_is_due(_cfg(path, max_age_days=1)) is True

    def test_a_database_with_no_meta_is_due(self, tmp_path):
        # A file exists but nothing recorded fetching it: treat it as unusable, not as
        # infinitely fresh, or a half-written database would never be repaired.
        path = str(tmp_path / "empty.sqlite")
        sqlite3.connect(path).close()
        assert registry_is_due(_cfg(path)) is True

    def test_an_unreadable_database_is_due_rather_than_fatal(self, tmp_path):
        path = tmp_path / "junk.sqlite"
        path.write_bytes(b"this is definitely not sqlite")
        assert registry_is_due(_cfg(str(path))) is True

    @pytest.mark.parametrize("days", [0, 1, 30])
    def test_max_age_is_honoured(self, tmp_path, days):
        import time

        path = str(tmp_path / f"reg{days}.sqlite")
        _db_with_meta(path, fetched_at=time.time() - 5 * 86400)
        expected = days <= 5
        assert registry_is_due(_cfg(path, max_age_days=days)) is expected
