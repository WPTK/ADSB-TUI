"""Build a local SQLite aircraft registry from public bulk sources.

This replaces the old "go find an FAA CSV yourself and point registry.path at it" story.
Two sources are supported, both free, both offline once fetched:

  * tar1090-db (https://github.com/wiedehopf/tar1090-db) -- a semicolon-delimited,
    header-less CSV of roughly 600k aircraft worldwide, about 8 MB gzipped. It carries
    registration, ICAO type code, a long type description, year, owner/operator and the
    readsb dbFlags bitfield, and crucially it covers NON-US aircraft, which an FAA extract
    never can. LICENSING: this data is Mictronics-derived and is licensed for
    NON-COMMERCIAL USE ONLY. Do not ship it, or anything built from it, commercially.

  * the FAA Releasable Aircraft Database
    (https://registry.faa.gov/database/ReleasableAircraft.zip) -- public domain, US only.
    MASTER.txt has the registered name and the Mode S hex; ACFTREF.txt has manufacturer
    and model, joined on MASTER's 'MFR MDL CODE' = ACFTREF's 'CODE'. Both members are
    read straight out of the zip; nothing is ever extracted to disk. Both are
    comma-delimited with a header row and space-padded fields, so every field is
    stripped and every header name is normalized before use.

Everything is written into a fresh temporary database that is os.replace()d into position
only once the build has fully succeeded, so an interrupted download or a truncated file
can never leave a user with a half-built registry: the previous one keeps working.
Rows belonging to OTHER sources are carried across on rebuild, so a user can run both
builders against one database file and keep both sets.

progress_fn(fraction, message) is optional everywhere. This module never imports curses
and works headlessly; the callback exists purely so a UI can draw a progress bar.
"""

from __future__ import annotations

import contextlib
import csv
import gzip
import io
import os
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

from adsbtui import __version__
from adsbtui.enrich.derived import normalize_hex

TAR1090_URL = "https://raw.githubusercontent.com/wiedehopf/tar1090-db/csv/aircraft.csv.gz"
FAA_URL = "https://registry.faa.gov/database/ReleasableAircraft.zip"

SOURCE_TAR1090 = "tar1090"
SOURCE_FAA = "faa"

#: Hard ceiling on a download. Both real sources are well under 100 MB; the cap exists so
#: a redirect to something enormous cannot fill the user's disk.
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024

_USER_AGENT = f"adsb-tui/{__version__} (+https://github.com/wptk/adsb-tui)"
_CHUNK = 64 * 1024
_BATCH = 5000

ProgressFn = Callable[[float, str], None]

#: Everything a malformed source file can throw at us while it is being parsed. Truncated
#: gzip data surfaces as EOFError or zlib.error, neither of which is an OSError.
_READ_ERRORS = (OSError, EOFError, csv.Error, UnicodeError, zipfile.BadZipFile, zlib.error)


class RegistryError(Exception):
    """A registry build or download failed. The database on disk is left untouched."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS aircraft (
    hex TEXT PRIMARY KEY,
    registration TEXT,
    type_code TEXT,
    type_desc TEXT,
    owner TEXT,
    year TEXT,
    flags INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aircraft_registration ON aircraft (registration);
CREATE TABLE IF NOT EXISTS meta (
    source TEXT PRIMARY KEY,
    url TEXT,
    fetched_at REAL NOT NULL,
    row_count INTEGER NOT NULL,
    etag TEXT,
    last_modified TEXT
);
"""

_INSERT = """
INSERT OR REPLACE INTO aircraft
    (hex, registration, type_code, type_desc, owner, year, flags, source)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

#: One built row, in _INSERT's column order.
Row = tuple[str, str | None, str | None, str | None, str | None, str | None, int, str]


@dataclass(frozen=True)
class SourceMeta:
    """What the meta table remembers about one source's last successful build."""

    source: str
    url: str | None
    fetched_at: float
    row_count: int
    etag: str | None = None
    last_modified: str | None = None

    def age_s(self, now: float | None = None) -> float:
        """Seconds since this source was last fetched, for staleness reporting."""
        return max(0.0, (time.time() if now is None else now) - self.fetched_at)


@dataclass(frozen=True)
class DownloadResult:
    path: str
    size: int
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True)
class BuildResult:
    source: str
    db_path: str
    row_count: int
    not_modified: bool = False


# --------------------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------------------


def is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


def download(
    url: str,
    dest: str,
    progress_fn: ProgressFn | None = None,
    *,
    timeout: float = 60.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    etag: str | None = None,
    last_modified: str | None = None,
) -> DownloadResult:
    """Fetch url into dest, optionally conditionally.

    Passing the etag and/or Last-Modified value recorded by a previous build turns this
    into a conditional request: an unchanged file costs one 304 response instead of 8 MB,
    which is what makes a routine "is my registry current?" check cheap. In that case the
    result has not_modified=True and dest is left untouched.

    The body is streamed to a temporary file next to dest and os.replace()d into place at
    the end, so dest is never a partial file. A declared or actual body larger than
    max_bytes is refused. Every network and filesystem failure becomes a RegistryError.
    """
    headers = {
        "User-Agent": _USER_AGENT,
        # Ask for the bytes as-is: the sources are already compressed, and an identity
        # encoding keeps Content-Length meaningful for the progress bar and the size cap.
        "Accept-Encoding": "identity",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    request = urllib.request.Request(url, headers=headers)
    directory = os.path.dirname(os.path.abspath(dest))
    os.makedirs(directory, exist_ok=True)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = _content_length(response)
            if total is not None and total > max_bytes:
                raise RegistryError(
                    f"{url} declared {total} bytes, over the {max_bytes} byte limit"
                )

            new_etag = response.headers.get("ETag")
            new_last_modified = response.headers.get("Last-Modified")

            fd, tmp_path = tempfile.mkstemp(prefix=".download-", dir=directory)
            written = 0
            try:
                with os.fdopen(fd, "wb") as out:
                    while True:
                        chunk = response.read(_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            raise RegistryError(
                                f"{url} exceeded the {max_bytes} byte limit while downloading"
                            )
                        out.write(chunk)
                        if progress_fn is not None and total:
                            progress_fn(min(written / total, 1.0), f"downloading {url}")
                os.replace(tmp_path, dest)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return DownloadResult(
                path=dest,
                size=0,
                not_modified=True,
                etag=etag,
                last_modified=last_modified,
            )
        raise RegistryError(f"could not download {url}: HTTP {exc.code} {exc.reason}") from exc
    except RegistryError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RegistryError(f"could not download {url}: {exc}") from exc

    if progress_fn is not None:
        progress_fn(1.0, f"downloaded {url}")

    return DownloadResult(
        path=dest,
        size=written,
        not_modified=False,
        etag=new_etag,
        last_modified=new_last_modified,
    )


def _content_length(response: object) -> int | None:
    raw = getattr(response, "headers", {}).get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# Reading the meta table
# --------------------------------------------------------------------------------------


def read_meta(db_path: str) -> dict[str, SourceMeta]:
    """Return what each source's last build recorded, keyed by source name.

    An absent, unreadable or pre-schema database is simply an empty dict: callers use
    this to report registry age, and a missing registry is a normal state, not an error.
    """
    if not os.path.exists(db_path):
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT source, url, fetched_at, row_count, etag, last_modified FROM meta"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}

    return {
        row[0]: SourceMeta(
            source=row[0],
            url=row[1],
            fetched_at=float(row[2]),
            row_count=int(row[3]),
            etag=row[4],
            last_modified=row[5],
        )
        for row in rows
    }


# --------------------------------------------------------------------------------------
# Writing the database
# --------------------------------------------------------------------------------------


def _batched(rows: Iterable[Row], size: int) -> Iterator[list[Row]]:
    batch: list[Row] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _preserve_other_sources(conn: sqlite3.Connection, db_path: str, source: str) -> None:
    """Copy rows and meta belonging to other sources out of the existing database.

    INSERT OR IGNORE, run after the new rows are in, so the source being rebuilt wins any
    hex collision. An existing database we cannot read (corrupt, or an older schema) is
    silently skipped -- it was unusable anyway, and we are about to replace it.
    """
    if not os.path.exists(db_path):
        return
    try:
        conn.execute("ATTACH DATABASE ? AS previous", (db_path,))
    except sqlite3.Error:
        return
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO aircraft
                (hex, registration, type_code, type_desc, owner, year, flags, source)
            SELECT hex, registration, type_code, type_desc, owner, year, flags, source
            FROM previous.aircraft WHERE source <> ?
            """,
            (source,),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO meta (source, url, fetched_at, row_count, etag, last_modified)
            SELECT source, url, fetched_at, row_count, etag, last_modified
            FROM previous.meta WHERE source <> ?
            """,
            (source,),
        )
    except sqlite3.Error:
        pass
    finally:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("DETACH DATABASE previous")


def _write_db(
    db_path: str,
    source: str,
    url: str | None,
    etag: str | None,
    last_modified: str | None,
    rows: Iterable[Row],
) -> int:
    """Build a complete database from rows and atomically move it onto db_path.

    Raises RegistryError if the input yielded no usable rows -- a valid-but-empty file
    almost always means the source moved or the download was garbage, and silently
    replacing a good registry with an empty one is exactly the failure this milestone is
    supposed to eliminate.
    """
    directory = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".registry-", suffix=".db", dir=directory)
    os.close(fd)

    try:
        conn = sqlite3.connect(tmp_path)
        try:
            conn.executescript(_SCHEMA)
            count = 0
            for batch in _batched(rows, _BATCH):
                conn.executemany(_INSERT, batch)
                count += len(batch)
            if count == 0:
                raise RegistryError(f"{source} source produced no usable rows")

            _preserve_other_sources(conn, db_path, source)
            conn.execute(
                """
                INSERT OR REPLACE INTO meta
                    (source, url, fetched_at, row_count, etag, last_modified)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source, url, time.time(), count, etag, last_modified),
            )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_path, db_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp_path)
        raise

    return count


# --------------------------------------------------------------------------------------
# tar1090-db
# --------------------------------------------------------------------------------------


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _tar1090_rows(path: str, progress_fn: ProgressFn | None) -> Iterator[Row]:
    """Yield rows from a tar1090-db CSV.

    The file has no header and seven semicolon-separated columns (plus a trailing
    separator): hex;registration;icao_type;flags;long_type_description;year;owner_operator
    Progress is measured against the position in the COMPRESSED file, which is why the
    gzip stream is layered over a file object we keep a handle on.
    """
    total = max(os.path.getsize(path), 1)

    with open(path, "rb") as raw:
        magic = raw.read(2)
        raw.seek(0)
        stream: io.BufferedIOBase = (
            gzip.GzipFile(fileobj=raw) if magic == b"\x1f\x8b" else raw  # type: ignore[assignment]
        )
        text = io.TextIOWrapper(stream, encoding="utf-8", errors="replace", newline="")
        for index, row in enumerate(csv.reader(text, delimiter=";")):
            if len(row) < 4:
                continue
            hex_id = normalize_hex(row[0])
            if hex_id is None:
                continue

            try:
                flags = int(row[3].strip() or "0", 16)
            except ValueError:
                flags = 0

            yield (
                hex_id,
                _clean(row[1]),
                _clean(row[2]),
                _clean(row[4]) if len(row) > 4 else None,
                _clean(row[6]) if len(row) > 6 else None,
                _clean(row[5]) if len(row) > 5 else None,
                flags,
                SOURCE_TAR1090,
            )

            if progress_fn is not None and index % _BATCH == 0:
                progress_fn(min(raw.tell() / total, 1.0), "indexing tar1090-db")


def build_from_tar1090(
    gz_path_or_url: str,
    db_path: str,
    progress_fn: ProgressFn | None = None,
    *,
    timeout: float = 60.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    force: bool = False,
) -> BuildResult:
    """Build (or rebuild) the tar1090-db half of the registry at db_path.

    gz_path_or_url may be an http(s) URL or a local path to the gzipped CSV. For a URL,
    the etag/Last-Modified from the previous build are replayed as a conditional request
    unless force=True, so an unchanged source returns not_modified=True having transferred
    almost nothing. Rows from other sources already in db_path are preserved.
    """
    return _build(
        gz_path_or_url,
        db_path,
        SOURCE_TAR1090,
        _tar1090_rows,
        progress_fn,
        timeout=timeout,
        max_bytes=max_bytes,
        force=force,
    )


# --------------------------------------------------------------------------------------
# FAA Releasable Aircraft Database
# --------------------------------------------------------------------------------------


def _find_member(archive: zipfile.ZipFile, wanted: str) -> str:
    """Locate a member by basename, case-insensitively (the FAA zip has shouted in both)."""
    for name in archive.namelist():
        if os.path.basename(name).lower() == wanted.lower():
            return name
    raise RegistryError(f"{wanted} is missing from the FAA archive")


def _header_index(header: list[str] | None) -> dict[str, int]:
    """Map normalized (stripped, uppercased) FAA column names to their position."""
    if not header:
        return {}
    return {name.strip().upper(): index for index, name in enumerate(header)}


def _field(row: list[str], columns: dict[str, int], name: str) -> str | None:
    index = columns.get(name)
    if index is None or index >= len(row):
        return None
    return _clean(row[index])


def _open_member(archive: zipfile.ZipFile, name: str) -> io.TextIOWrapper:
    # utf-8-sig strips the BOM the real FAA extract carries (same reason normalize.py uses
    # it); errors="replace" because the file is nominally ASCII but has stray bytes, and one
    # bad owner name must not abort a 300k row build.
    return io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", errors="replace", newline="")


def _read_acftref(archive: zipfile.ZipFile) -> dict[str, str]:
    """Build the 'MFR MDL CODE' -> 'manufacturer model' lookup from ACFTREF.txt."""
    name = _find_member(archive, "ACFTREF.txt")
    types: dict[str, str] = {}
    with _open_member(archive, name) as text:
        reader = csv.reader(text)
        columns = _header_index(next(reader, None))
        if "CODE" not in columns:
            raise RegistryError("ACFTREF.txt has no CODE column")
        for row in reader:
            code = _field(row, columns, "CODE")
            if not code:
                continue
            parts = [
                part
                for part in (_field(row, columns, "MFR"), _field(row, columns, "MODEL"))
                if part
            ]
            if parts:
                types[code] = " ".join(parts)
    return types


def _faa_rows(path: str, progress_fn: ProgressFn | None) -> Iterator[Row]:
    """Yield rows from an FAA ReleasableAircraft.zip, joining MASTER to ACFTREF."""
    with zipfile.ZipFile(path) as archive:
        if progress_fn is not None:
            progress_fn(0.0, "reading FAA aircraft reference")
        types = _read_acftref(archive)

        master_name = _find_member(archive, "MASTER.txt")
        total = max(archive.getinfo(master_name).file_size, 1)
        with _open_member(archive, master_name) as text:
            reader = csv.reader(text)
            columns = _header_index(next(reader, None))
            for required in ("N-NUMBER", "MODE S CODE HEX"):
                if required not in columns:
                    raise RegistryError(f"MASTER.txt has no {required} column")

            for index, row in enumerate(reader):
                hex_id = _field(row, columns, "MODE S CODE HEX")
                hex_id = normalize_hex(hex_id) if hex_id else None
                if hex_id is None:
                    continue

                n_number = _field(row, columns, "N-NUMBER")
                model_code = _field(row, columns, "MFR MDL CODE")

                yield (
                    hex_id,
                    f"N{n_number}" if n_number else None,
                    None,  # the FAA extract has no ICAO type code, only make and model
                    types.get(model_code or ""),
                    _field(row, columns, "NAME"),
                    _field(row, columns, "YEAR MFR"),
                    0,
                    SOURCE_FAA,
                )

                if progress_fn is not None and index % _BATCH == 0:
                    progress_fn(min(text.buffer.tell() / total, 1.0), "indexing FAA registry")


def build_from_faa_zip(
    zip_path_or_url: str,
    db_path: str,
    progress_fn: ProgressFn | None = None,
    *,
    timeout: float = 120.0,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
    force: bool = False,
) -> BuildResult:
    """Build (or rebuild) the FAA half of the registry at db_path.

    zip_path_or_url may be an http(s) URL or a local path to ReleasableAircraft.zip. The
    conditional-request and other-source-preservation behavior matches
    build_from_tar1090().
    """
    return _build(
        zip_path_or_url,
        db_path,
        SOURCE_FAA,
        _faa_rows,
        progress_fn,
        timeout=timeout,
        max_bytes=max_bytes,
        force=force,
    )


# --------------------------------------------------------------------------------------
# Shared build driver
# --------------------------------------------------------------------------------------


def _build(
    source_path_or_url: str,
    db_path: str,
    source: str,
    row_reader: Callable[[str, ProgressFn | None], Iterator[Row]],
    progress_fn: ProgressFn | None,
    *,
    timeout: float,
    max_bytes: int,
    force: bool,
) -> BuildResult:
    """Fetch if needed, parse, and atomically install one source's rows."""
    previous = read_meta(db_path).get(source)
    scratch: str | None = None
    url: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    try:
        if is_url(source_path_or_url):
            url = source_path_or_url
            scratch = tempfile.mkdtemp(prefix="adsbtui-registry-")
            local_path = os.path.join(scratch, "download")
            result = download(
                url,
                local_path,
                progress_fn,
                timeout=timeout,
                max_bytes=max_bytes,
                etag=None if force or previous is None else previous.etag,
                last_modified=None if force or previous is None else previous.last_modified,
            )
            if result.not_modified:
                return BuildResult(
                    source=source,
                    db_path=db_path,
                    row_count=previous.row_count if previous else 0,
                    not_modified=True,
                )
            etag = result.etag
            last_modified = result.last_modified
        else:
            local_path = source_path_or_url
            if not os.path.exists(local_path):
                raise RegistryError(f"{local_path} does not exist")

        try:
            count = _write_db(
                db_path, source, url, etag, last_modified, row_reader(local_path, progress_fn)
            )
        except RegistryError:
            raise
        except _READ_ERRORS as exc:
            raise RegistryError(f"could not read {source_path_or_url}: {exc}") from exc
        except sqlite3.Error as exc:
            raise RegistryError(f"could not write {db_path}: {exc}") from exc
    finally:
        if scratch is not None:
            _rmtree(scratch)

    if progress_fn is not None:
        progress_fn(1.0, f"{source}: {count} aircraft")

    return BuildResult(source=source, db_path=db_path, row_count=count)


def _rmtree(directory: str) -> None:
    """Remove a scratch directory and its contents, ignoring anything already gone."""
    for name in os.listdir(directory):
        with contextlib.suppress(OSError):
            os.remove(os.path.join(directory, name))
    with contextlib.suppress(OSError):
        os.rmdir(directory)
