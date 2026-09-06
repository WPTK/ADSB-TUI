"""Tests for adsbtui.enrich.registry_update: building the local SQLite registry.

Nothing here touches the network. The bulk sources are reproduced as tiny fixtures written
into tmp_path (a gzipped semicolon CSV shaped like tar1090-db, a zip shaped like the FAA
ReleasableAircraft extract), and the two download paths are exercised by monkeypatching
urllib.request.urlopen.
"""

from __future__ import annotations

import gzip
import io
import os
import sqlite3
import urllib.error
import urllib.request
import zipfile

import pytest

from adsbtui.enrich import registry_update
from adsbtui.enrich.registry_update import (
    SOURCE_FAA,
    SOURCE_TAR1090,
    BuildResult,
    RegistryError,
    build_from_faa_zip,
    build_from_tar1090,
    download,
    read_meta,
)

TAR1090_SAMPLE = (
    "A004B3;N100;J3;00;PIPER J-3 Cub;1940;BENE MARY D;\n"
    "3C6444;D-AIBD;A319;00;AIRBUS A-319;;;\n"
    "AE1234;;C130;01;LOCKHEED C-130;1988;US AIR FORCE;\n"
)


def write_tar1090(path, text=TAR1090_SAMPLE):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(text)
    return str(path)


MASTER_ROWS = [
    "N-NUMBER,SERIAL NUMBER,MFR MDL CODE,YEAR MFR,NAME,MODE S CODE HEX,",
    "100        ,12345    ,05601123 ,1940 ,BENE MARY D               ,A004B3 ,",
    "813UA      ,99999    ,13900543 ,1998 ,UNITED AIR LINES INC      ,AB1644 ,",
    "999ZZ      ,55555    ,00000000 ,     ,NO SUCH TYPE CO           ,A7C123 ,",
    "404NF      ,44444    ,05601123 ,1975 ,MISSING HEX CO            ,       ,",
]

ACFTREF_ROWS = [
    "CODE,MFR,MODEL,",
    "05601123 ,PIPER                    ,J-3        ,",
    "13900543 ,BOEING                   ,777-222    ,",
]


def write_faa_zip(path, master=None, acftref=None, omit=()):
    master = MASTER_ROWS if master is None else master
    acftref = ACFTREF_ROWS if acftref is None else acftref
    with zipfile.ZipFile(path, "w") as archive:
        if "MASTER.txt" not in omit:
            archive.writestr("MASTER.txt", "\n".join(master) + "\n")
        if "ACFTREF.txt" not in omit:
            archive.writestr("ACFTREF.txt", "\n".join(acftref) + "\n")
    return str(path)


def rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        return {row["hex"]: dict(row) for row in conn.execute("SELECT * FROM aircraft")}
    finally:
        conn.close()


class TestBuildFromTar1090:
    def test_builds_expected_rows(self, tmp_path):
        source = write_tar1090(tmp_path / "aircraft.csv.gz")
        db_path = str(tmp_path / "registry.db")

        result = build_from_tar1090(source, db_path)

        assert isinstance(result, BuildResult)
        assert result.row_count == 3
        assert result.not_modified is False

        built = rows(db_path)
        assert set(built) == {"a004b3", "3c6444", "ae1234"}

        piper = built["a004b3"]
        assert piper["registration"] == "N100"
        assert piper["type_code"] == "J3"
        assert piper["type_desc"] == "PIPER J-3 Cub"
        assert piper["owner"] == "BENE MARY D"
        assert piper["year"] == "1940"
        assert piper["flags"] == 0
        assert piper["source"] == SOURCE_TAR1090

        # Non-US aircraft are the whole reason this source exists.
        assert built["3c6444"]["registration"] == "D-AIBD"
        assert built["3c6444"]["year"] is None

        # The flags column is hex with readsb dbFlags bit meanings.
        assert built["ae1234"]["flags"] == 0x1

    def test_meta_records_the_build(self, tmp_path):
        source = write_tar1090(tmp_path / "aircraft.csv.gz")
        db_path = str(tmp_path / "registry.db")

        build_from_tar1090(source, db_path)
        meta = read_meta(db_path)

        assert set(meta) == {SOURCE_TAR1090}
        assert meta[SOURCE_TAR1090].row_count == 3
        assert meta[SOURCE_TAR1090].url is None  # a local file, not a download
        assert meta[SOURCE_TAR1090].age_s() < 60

    def test_accepts_a_plain_uncompressed_csv(self, tmp_path):
        source = tmp_path / "aircraft.csv"
        source.write_text(TAR1090_SAMPLE, encoding="utf-8")
        db_path = str(tmp_path / "registry.db")

        assert build_from_tar1090(str(source), db_path).row_count == 3

    def test_skips_unusable_rows(self, tmp_path):
        source = write_tar1090(
            tmp_path / "aircraft.csv.gz",
            "NOTHEX;N1;;00;;;;\nA004B3;N100;J3;zz;PIPER J-3 Cub;1940;BENE MARY D;\ntruncated\n",
        )
        db_path = str(tmp_path / "registry.db")

        assert build_from_tar1090(source, db_path).row_count == 1
        # An unparseable flags field falls back to 0 rather than dropping the aircraft.
        assert rows(db_path)["a004b3"]["flags"] == 0

    def test_rebuild_replaces_the_previous_contents(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        first = write_tar1090(tmp_path / "first.csv.gz")
        build_from_tar1090(first, db_path)

        second = write_tar1090(
            tmp_path / "second.csv.gz", "C01234;C-GABC;DH8D;00;DE HAVILLAND Dash 8;2001;AIR CO;\n"
        )
        result = build_from_tar1090(second, db_path)

        assert result.row_count == 1
        assert set(rows(db_path)) == {"c01234"}
        assert read_meta(db_path)[SOURCE_TAR1090].row_count == 1

    def test_progress_callback_is_optional_and_bounded(self, tmp_path):
        source = write_tar1090(tmp_path / "aircraft.csv.gz")
        db_path = str(tmp_path / "registry.db")
        seen = []

        build_from_tar1090(source, db_path, lambda fraction, message: seen.append(fraction))

        assert seen, "progress_fn was never called"
        assert all(0.0 <= fraction <= 1.0 for fraction in seen)
        assert seen[-1] == 1.0


class TestBuildFailures:
    def test_missing_source_file(self, tmp_path):
        with pytest.raises(RegistryError):
            build_from_tar1090(str(tmp_path / "nope.csv.gz"), str(tmp_path / "registry.db"))

    def test_truncated_gzip_leaves_the_previous_database_intact(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        build_from_tar1090(write_tar1090(tmp_path / "good.csv.gz"), db_path)
        before = rows(db_path)

        broken = tmp_path / "broken.csv.gz"
        payload = gzip.compress(("A004B3;N100;J3;00;X;1940;Y;\n" * 500).encode())
        broken.write_bytes(payload[: len(payload) // 2])

        with pytest.raises(RegistryError):
            build_from_tar1090(str(broken), db_path)

        assert rows(db_path) == before
        assert read_meta(db_path)[SOURCE_TAR1090].row_count == 3
        assert not _leftovers(tmp_path)

    def test_empty_source_does_not_replace_a_good_database(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        build_from_tar1090(write_tar1090(tmp_path / "good.csv.gz"), db_path)

        empty = write_tar1090(tmp_path / "empty.csv.gz", "\n\n")
        with pytest.raises(RegistryError):
            build_from_tar1090(empty, db_path)

        assert len(rows(db_path)) == 3
        assert not _leftovers(tmp_path)

    def test_failed_first_build_leaves_no_database_behind(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        broken = tmp_path / "broken.csv.gz"
        broken.write_bytes(b"\x1f\x8b\x08\x00 not really a gzip stream")

        with pytest.raises(RegistryError):
            build_from_tar1090(str(broken), db_path)

        assert not os.path.exists(db_path)
        assert not _leftovers(tmp_path)

    def test_corrupt_zip(self, tmp_path):
        broken = tmp_path / "faa.zip"
        broken.write_bytes(b"PK\x03\x04 not actually a zip")

        with pytest.raises(RegistryError):
            build_from_faa_zip(str(broken), str(tmp_path / "registry.db"))

        assert not os.path.exists(str(tmp_path / "registry.db"))

    def test_zip_without_master(self, tmp_path):
        source = write_faa_zip(tmp_path / "faa.zip", omit=("MASTER.txt",))

        with pytest.raises(RegistryError, match="MASTER.txt"):
            build_from_faa_zip(source, str(tmp_path / "registry.db"))

    def test_zip_without_acftref(self, tmp_path):
        source = write_faa_zip(tmp_path / "faa.zip", omit=("ACFTREF.txt",))

        with pytest.raises(RegistryError, match="ACFTREF.txt"):
            build_from_faa_zip(source, str(tmp_path / "registry.db"))


def _leftovers(directory):
    """Temporary build/download files that should never survive a failed build."""
    return [name for name in os.listdir(directory) if name.startswith(".")]


class TestBuildFromFaaZip:
    def test_joins_master_to_acftref(self, tmp_path):
        source = write_faa_zip(tmp_path / "faa.zip")
        db_path = str(tmp_path / "registry.db")

        result = build_from_faa_zip(source, db_path)

        # The row with a blank Mode S hex is unusable and is dropped.
        assert result.row_count == 3
        built = rows(db_path)
        assert set(built) == {"a004b3", "ab1644", "a7c123"}

        piper = built["a004b3"]
        assert piper["registration"] == "N100"
        assert piper["owner"] == "BENE MARY D"
        assert piper["type_desc"] == "PIPER J-3"  # the free answer to "type needs a paid API"
        assert piper["year"] == "1940"
        assert piper["type_code"] is None  # the FAA extract has no ICAO type code
        assert piper["source"] == SOURCE_FAA

        assert built["ab1644"]["registration"] == "N813UA"
        assert built["ab1644"]["type_desc"] == "BOEING 777-222"
        # A model code with no ACFTREF row simply has no description.
        assert built["a7c123"]["type_desc"] is None

    def test_tolerates_a_bom_and_padded_headers(self, tmp_path):
        master = list(MASTER_ROWS)
        master[0] = "﻿" + master[0].replace("N-NUMBER", " N-NUMBER ")
        source = write_faa_zip(tmp_path / "faa.zip", master=master)
        db_path = str(tmp_path / "registry.db")

        build_from_faa_zip(source, db_path)

        assert rows(db_path)["a004b3"]["registration"] == "N100"

    def test_meta_records_the_build(self, tmp_path):
        source = write_faa_zip(tmp_path / "faa.zip")
        db_path = str(tmp_path / "registry.db")

        build_from_faa_zip(source, db_path)

        assert read_meta(db_path)[SOURCE_FAA].row_count == 3


class TestMultipleSources:
    def test_each_source_keeps_the_other_source_rows(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        build_from_tar1090(write_tar1090(tmp_path / "aircraft.csv.gz"), db_path)
        build_from_faa_zip(write_faa_zip(tmp_path / "faa.zip"), db_path)

        built = rows(db_path)
        # 3c6444 and ae1234 come only from tar1090-db; ab1644 only from the FAA extract.
        assert {"3c6444", "ae1234", "ab1644"} <= set(built)
        assert built["3c6444"]["source"] == SOURCE_TAR1090
        assert built["ab1644"]["source"] == SOURCE_FAA
        # The source being rebuilt wins a hex both sources carry.
        assert built["a004b3"]["source"] == SOURCE_FAA

        meta = read_meta(db_path)
        assert set(meta) == {SOURCE_TAR1090, SOURCE_FAA}
        assert meta[SOURCE_TAR1090].row_count == 3

    def test_rebuilding_one_source_does_not_disturb_the_other(self, tmp_path):
        db_path = str(tmp_path / "registry.db")
        build_from_faa_zip(write_faa_zip(tmp_path / "faa.zip"), db_path)
        build_from_tar1090(write_tar1090(tmp_path / "aircraft.csv.gz"), db_path)
        build_from_tar1090(
            write_tar1090(tmp_path / "again.csv.gz", "C01234;C-GABC;DH8D;00;Dash 8;2001;AIR CO;\n"),
            db_path,
        )

        built = rows(db_path)
        assert built["ab1644"]["source"] == SOURCE_FAA
        assert "c01234" in built
        assert "3c6444" not in built  # the rebuilt source's old rows are gone


class TestReadMeta:
    def test_missing_database(self, tmp_path):
        assert read_meta(str(tmp_path / "nope.db")) == {}

    def test_unreadable_database(self, tmp_path):
        path = tmp_path / "junk.db"
        path.write_bytes(b"this is not a sqlite database")
        assert read_meta(str(path)) == {}


# --------------------------------------------------------------------------------------
# Downloading, with urlopen replaced -- no test ever reaches the network.
# --------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, body, headers=None):
        self._stream = io.BytesIO(body)
        self.headers = headers or {}

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FakeOpener:
    """Stands in for urllib.request.urlopen and records the requests it was given."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def not_modified(url="https://example.invalid/aircraft.csv.gz"):
    return urllib.error.HTTPError(url, 304, "Not Modified", {}, None)


class TestDownload:
    def test_writes_the_body_and_reports_validators(self, tmp_path, monkeypatch):
        body = b"hello registry"
        opener = FakeOpener(
            FakeResponse(
                body,
                {"Content-Length": str(len(body)), "ETag": '"abc"', "Last-Modified": "Mon, 1 Jan"},
            )
        )
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        dest = str(tmp_path / "out" / "file.gz")

        result = download("https://example.invalid/x.gz", dest)

        assert result.not_modified is False
        assert result.size == len(body)
        assert result.etag == '"abc"'
        assert result.last_modified == "Mon, 1 Jan"
        with open(dest, "rb") as handle:
            assert handle.read() == body

    def test_sends_conditional_headers_and_handles_304(self, tmp_path, monkeypatch):
        opener = FakeOpener(not_modified())
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        dest = str(tmp_path / "file.gz")

        result = download(
            "https://example.invalid/x.gz", dest, etag='"abc"', last_modified="Mon, 1 Jan"
        )

        assert result.not_modified is True
        assert result.etag == '"abc"'
        assert not os.path.exists(dest)
        request = opener.requests[0]
        assert request.get_header("If-none-match") == '"abc"'
        assert request.get_header("If-modified-since") == "Mon, 1 Jan"

    def test_refuses_a_declared_oversize_body(self, tmp_path, monkeypatch):
        opener = FakeOpener(FakeResponse(b"x" * 100, {"Content-Length": "100"}))
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        dest = str(tmp_path / "file.gz")

        with pytest.raises(RegistryError, match="limit"):
            download("https://example.invalid/x.gz", dest, max_bytes=10)

        assert not os.path.exists(dest)
        assert not _leftovers(tmp_path)

    def test_refuses_an_undeclared_oversize_body(self, tmp_path, monkeypatch):
        opener = FakeOpener(FakeResponse(b"x" * 300000))
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        dest = str(tmp_path / "file.gz")

        with pytest.raises(RegistryError, match="limit"):
            download("https://example.invalid/x.gz", dest, max_bytes=1000)

        assert not os.path.exists(dest)
        assert not _leftovers(tmp_path)

    def test_network_failure_becomes_a_registry_error(self, tmp_path, monkeypatch):
        opener = FakeOpener(urllib.error.URLError("no route to host"))
        monkeypatch.setattr(urllib.request, "urlopen", opener)

        with pytest.raises(RegistryError, match="could not download"):
            download("https://example.invalid/x.gz", str(tmp_path / "file.gz"))

    def test_http_error_becomes_a_registry_error(self, tmp_path, monkeypatch):
        opener = FakeOpener(
            urllib.error.HTTPError("https://example.invalid/x.gz", 404, "Not Found", {}, None)
        )
        monkeypatch.setattr(urllib.request, "urlopen", opener)

        with pytest.raises(RegistryError, match="404"):
            download("https://example.invalid/x.gz", str(tmp_path / "file.gz"))


class TestBuildFromUrl:
    def test_downloads_and_records_the_url(self, tmp_path, monkeypatch):
        body = gzip.compress(TAR1090_SAMPLE.encode())
        opener = FakeOpener(FakeResponse(body, {"Content-Length": str(len(body)), "ETag": '"v1"'}))
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        db_path = str(tmp_path / "registry.db")

        result = build_from_tar1090("https://example.invalid/aircraft.csv.gz", db_path)

        assert result.row_count == 3
        meta = read_meta(db_path)[SOURCE_TAR1090]
        assert meta.url == "https://example.invalid/aircraft.csv.gz"
        assert meta.etag == '"v1"'

    def test_unchanged_source_is_a_cheap_no_op(self, tmp_path, monkeypatch):
        body = gzip.compress(TAR1090_SAMPLE.encode())
        opener = FakeOpener(
            FakeResponse(body, {"Content-Length": str(len(body)), "ETag": '"v1"'}),
            not_modified(),
        )
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        db_path = str(tmp_path / "registry.db")
        url = "https://example.invalid/aircraft.csv.gz"

        build_from_tar1090(url, db_path)
        first_fetch = read_meta(db_path)[SOURCE_TAR1090].fetched_at

        result = build_from_tar1090(url, db_path)

        assert result.not_modified is True
        assert result.row_count == 3
        assert opener.requests[1].get_header("If-none-match") == '"v1"'
        # A 304 must not rewrite the database or its recorded fetch time.
        assert read_meta(db_path)[SOURCE_TAR1090].fetched_at == first_fetch
        assert len(rows(db_path)) == 3

    def test_force_skips_the_conditional_headers(self, tmp_path, monkeypatch):
        body = gzip.compress(TAR1090_SAMPLE.encode())
        opener = FakeOpener(
            FakeResponse(body, {"ETag": '"v1"'}),
            FakeResponse(body, {"ETag": '"v1"'}),
        )
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        db_path = str(tmp_path / "registry.db")
        url = "https://example.invalid/aircraft.csv.gz"

        build_from_tar1090(url, db_path)
        build_from_tar1090(url, db_path, force=True)

        assert opener.requests[1].get_header("If-none-match") is None

    def test_a_failed_download_leaves_no_scratch_directory(self, tmp_path, monkeypatch):
        opener = FakeOpener(urllib.error.URLError("boom"))
        monkeypatch.setattr(urllib.request, "urlopen", opener)
        before = set(os.listdir(registry_update.tempfile.gettempdir()))

        with pytest.raises(RegistryError):
            build_from_tar1090("https://example.invalid/x.gz", str(tmp_path / "registry.db"))

        after = set(os.listdir(registry_update.tempfile.gettempdir()))
        assert not {name for name in after - before if name.startswith("adsbtui-registry-")}
