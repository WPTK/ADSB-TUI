"""Pure-stdlib sqlite3 persistence for closed-track "sightings" and simple stats.

A "sighting" is one row summarizing an aircraft track's whole lifetime, from the moment
the tracker first saw it to the moment the tracker decided the track had closed (gone
stale/lost for long enough that it will not be re-associated with new updates). The
tracker decides when a track has closed and accumulates the summary stats -- min
distance, max altitude, max ground speed, squawks seen, whether it ever emergency
squawked -- across the track's lifetime; this module only knows how to persist one
finished record (record_close), read them back for reporting (get_stats,
export_unknown), and garbage-collect old ones (prune_old).

Nothing here touches curses or the network, and there is no module-level state: every
function takes a db_path and opens/closes its own short-lived connection, so this module
is safe to call from any thread and easy to unit test without a terminal.
"""

from __future__ import annotations

import csv
import os
import sqlite3
from dataclasses import dataclass

from adsbtui.model import Aircraft

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sightings (
    hex TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    min_distance_mi REAL,
    min_distance_at REAL,
    max_altitude_ft REAL,
    max_gs_kt REAL,
    registration TEXT,
    callsign TEXT,
    type_code TEXT,
    owner_name TEXT,
    squawks TEXT,
    had_emergency INTEGER NOT NULL DEFAULT 0,
    db_flags INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hex, first_seen)
)
"""


def _connect(db_path: str) -> sqlite3.Connection:
    """Open a connection to db_path, creating its parent directory if needed."""
    dirname = os.path.dirname(db_path)
    if dirname:
        os.makedirs(dirname, exist_ok=True)
    return sqlite3.connect(db_path)


def ensure_schema(db_path: str) -> None:
    """Create the sightings table (and its parent directory) if it does not exist yet.

    Sets PRAGMA journal_mode=WAL for better concurrent read/write behavior. Idempotent
    and cheap enough to call unconditionally on every startup, and defensively before
    every write in this module.
    """
    conn = _connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def record_close(
    db_path: str,
    aircraft: Aircraft,
    first_seen: float,
    last_seen: float,
    min_distance_mi: float | None,
    min_distance_at: float | None,
    max_altitude_ft: float | None,
    max_gs_kt: float | None,
    squawks_seen: list[str] | None,
    had_emergency: bool,
) -> None:
    """Persist one row summarizing a track that just closed.

    `aircraft` supplies the identifying/descriptive fields (hex, flight, registration,
    type_code, owner_name, db_flags); the remaining arguments are the summary stats the
    caller accumulated across the track's whole lifetime. `squawks_seen` is stored as a
    comma-joined string (empty/None becomes an empty string).

    Uses INSERT OR REPLACE keyed on (hex, first_seen): if the same track is somehow
    closed twice with the same first_seen (e.g. a caller retries after a crash), the
    later call simply overwrites the earlier row rather than raising or duplicating it.

    ensure_schema() is called every time here (CREATE TABLE IF NOT EXISTS is cheap) so
    this function works even if the caller never called ensure_schema() itself.
    """
    ensure_schema(db_path)
    squawks_text = ",".join(squawks_seen) if squawks_seen else ""

    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO sightings (
                hex, first_seen, last_seen, min_distance_mi, min_distance_at,
                max_altitude_ft, max_gs_kt, registration, callsign, type_code,
                owner_name, squawks, had_emergency, db_flags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                aircraft.hex,
                first_seen,
                last_seen,
                min_distance_mi,
                min_distance_at,
                max_altitude_ft,
                max_gs_kt,
                aircraft.registration,
                aircraft.flight,
                aircraft.type_code,
                aircraft.owner_name,
                squawks_text,
                1 if had_emergency else 0,
                aircraft.db_flags,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def prune_old(db_path: str, retention_days: float, now: float) -> int:
    """Delete sightings whose last_seen is older than retention_days before now.

    Returns the number of rows deleted. Safe to call on a database that does not exist
    yet or has no matching rows (returns 0).
    """
    ensure_schema(db_path)
    cutoff = now - retention_days * 86400

    conn = _connect(db_path)
    try:
        cursor = conn.execute("DELETE FROM sightings WHERE last_seen < ?", (cutoff,))
        conn.commit()
        return cursor.rowcount if cursor.rowcount is not None and cursor.rowcount >= 0 else 0
    finally:
        conn.close()


@dataclass
class Stats:
    """Aggregate stats over a set of sightings, as computed by get_stats()."""

    unique_count: int
    closest: tuple[str, float, float] | None  # (hex, min_distance_mi, min_distance_at)
    highest_altitude: tuple[str, float] | None  # (hex, max_altitude_ft)
    fastest: tuple[str, float] | None  # (hex, max_gs_kt)
    emergency_count: int


def get_stats(db_path: str, since: float) -> Stats:
    """Compute aggregate Stats over sightings whose first_seen is at or after `since`.

    Returns a Stats with unique_count=0, emergency_count=0, and the tuple fields set to
    None when there are no matching rows (never raises on an empty table).
    """
    ensure_schema(db_path)

    conn = _connect(db_path)
    try:
        (unique_count,) = conn.execute(
            "SELECT COUNT(*) FROM sightings WHERE first_seen >= ?", (since,)
        ).fetchone()

        (emergency_count,) = conn.execute(
            "SELECT COUNT(*) FROM sightings WHERE first_seen >= ? AND had_emergency = 1",
            (since,),
        ).fetchone()

        closest_row = conn.execute(
            """
            SELECT hex, min_distance_mi, min_distance_at
            FROM sightings
            WHERE first_seen >= ? AND min_distance_mi IS NOT NULL
            ORDER BY min_distance_mi ASC
            LIMIT 1
            """,
            (since,),
        ).fetchone()

        highest_row = conn.execute(
            """
            SELECT hex, max_altitude_ft
            FROM sightings
            WHERE first_seen >= ? AND max_altitude_ft IS NOT NULL
            ORDER BY max_altitude_ft DESC
            LIMIT 1
            """,
            (since,),
        ).fetchone()

        fastest_row = conn.execute(
            """
            SELECT hex, max_gs_kt
            FROM sightings
            WHERE first_seen >= ? AND max_gs_kt IS NOT NULL
            ORDER BY max_gs_kt DESC
            LIMIT 1
            """,
            (since,),
        ).fetchone()
    finally:
        conn.close()

    return Stats(
        unique_count=unique_count,
        closest=tuple(closest_row) if closest_row is not None else None,
        highest_altitude=tuple(highest_row) if highest_row is not None else None,
        fastest=tuple(fastest_row) if fastest_row is not None else None,
        emergency_count=emergency_count,
    )


def export_unknown(db_path: str, out_path: str) -> int:
    """Write a CSV of hex/callsign/count/last_seen for aircraft with no known owner.

    "No known owner" means owner_name is NULL or the empty string. Rows are grouped by
    hex: count is the number of sightings rows for that hex, last_seen is the latest of
    those, and callsign is a representative one (MAX(callsign) among that hex's rows --
    an arbitrary but deterministic pick when several distinct callsigns were seen).

    Writes a header row ("hex", "callsign", "count", "last_seen") followed by one data
    row per distinct hex, ordered by hex. Returns the number of data rows written (not
    counting the header). Creates out_path's parent directory if needed.
    """
    ensure_schema(db_path)

    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT hex, MAX(callsign), COUNT(*), MAX(last_seen)
            FROM sightings
            WHERE owner_name IS NULL OR owner_name = ''
            GROUP BY hex
            ORDER BY hex
            """
        ).fetchall()
    finally:
        conn.close()

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["hex", "callsign", "count", "last_seen"])
        for hex_id, callsign, count, last_seen in rows:
            writer.writerow([hex_id, callsign or "", count, last_seen])

    return len(rows)
