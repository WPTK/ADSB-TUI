"""Tests for adsbtui.output: the non-curses table/json/csv formatters and the
--once/--watch/--headless/--batch runners.

Uses a real FileSource against tests/fixtures/aircraft.json and a real Config built
through adsbtui.config.build_config() (allow_null_home=True as the escape hatch, paired
with real home coordinates so distance/bearing/CPA actually compute).

The headless-service tests inject a recording dispatcher and a scripted source, so no test
here ever beeps, POSTs a webhook, or runs a command: make_cfg() also pins alerts.bell off
and leaves webhook_url/command empty, so even the tests that build a real
dispatch.Dispatcher have every channel disabled.
"""

from __future__ import annotations

import io
import json
import logging
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from adsbtui import output
from adsbtui.config import build_config
from adsbtui.model import AlertLevel
from adsbtui.sources.jsonfile import FileSource

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "aircraft.json"

# Home coordinates chosen so several (but not all) of the fixture's aircraft fall inside
# the default 15-mile filter radius -- see the fixture's lat/lon values.
HOME_LAT = 37.7749
HOME_LON = -122.4194

# UAL123 (hex a1b2c3) is ~5.3mi from HOME_LAT/HOME_LON and has gs=460.0kt -- the aircraft
# the original tool's speed-unit bug (km/h math mislabeled as mph) would have gotten
# wrong. It survives the default filter.radius=15.0mi filter.
KNOWN_HEX = "a1b2c3"
KNOWN_FLIGHT = "UAL123"
KNOWN_GS_KT = 460.0
KT_TO_MPH = 1.150779448

# AAL911 (hex b00001) squawks 7700 with emergency="general" ~12.7mi out -- the fixture's
# one aircraft that grades EMERGENCY, and so the one that must reach a dispatcher.
EMERGENCY_HEX = "b00001"

# The ground vehicle at ~10.9mi: far enough out to grade AlertLevel.NONE, which makes it
# the aircraft to use when testing that a watchlist hit fires on its own (a hit is only
# reported as a "watchlist" event when no alert-level transition already covered it).
GROUND_HEX = "a4d5e6"


def make_cfg():
    return build_config(
        toml_dict={},
        env={},
        cli={
            "source.url": str(FIXTURE_PATH),
            "home.lat": HOME_LAT,
            "home.lon": HOME_LON,
            # Never touch the developer's real watchlist/log, and never ring a real bell:
            # tests that want a watchlist point this at a tmp_path file of their own.
            "watchlist.path": "",
            "logging.file": "",
            "alerts.bell": False,
        },
        allow_null_home=True,
    )


def make_source():
    return FileSource(str(FIXTURE_PATH), max_bytes=8_000_000)


class _FailingSource:
    """A Source whose fetch() always raises a SourceError, for error-path tests."""

    def fetch(self, timeout: float) -> dict:
        from adsbtui.sources import SourceUnreachable

        raise SourceUnreachable("simulated failure")


@pytest.fixture
def cfg():
    return make_cfg()


@pytest.fixture
def source():
    return make_source()


# --------------------------------------------------------------------------------------
# fetch_and_process
# --------------------------------------------------------------------------------------


class TestFetchAndProcess:
    def test_success_returns_aircraft_and_no_error(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        assert len(aircraft_list) > 0
        assert all(ac.distance_mi is not None for ac in aircraft_list)

    def test_known_aircraft_present(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        hexes = {ac.hex for ac in aircraft_list}
        assert KNOWN_HEX in hexes

    def test_far_aircraft_filtered_out_by_radius(self, cfg, source):
        # adf7c8 (RCH245) is ~27.7mi out -- well beyond the default 15mi filter.radius,
        # and it carries no emergency squawk/state, so the radius filter should drop it.
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        hexes = {ac.hex for ac in aircraft_list}
        assert "adf7c8" not in hexes

    def test_source_error_yields_empty_list_and_exception(self, cfg):
        aircraft_list, err = output.fetch_and_process(_FailingSource(), cfg)
        assert aircraft_list == []
        assert isinstance(err, Exception)
        assert "simulated failure" in str(err)


# --------------------------------------------------------------------------------------
# aircraft_to_dict / render_json -- the original mph-bug regression test lives here
# --------------------------------------------------------------------------------------


class TestAircraftToDict:
    def test_flat_and_json_safe(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        d = output.aircraft_to_dict(aircraft_list[0], cfg.display.units)
        # Must round-trip through json.dumps with no TypeError.
        json.dumps(d)
        assert isinstance(d, dict)
        assert "hex" in d and "altitude_ft" in d and "ground_speed_kt" in d

    def test_ground_speed_mph_matches_expected_conversion(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        by_hex = {ac.hex: ac for ac in aircraft_list}
        ac = by_hex[KNOWN_HEX]
        assert ac.ground_speed_kt == pytest.approx(KNOWN_GS_KT)

        d = output.aircraft_to_dict(ac, "imperial")
        assert d["ground_speed_kt"] == pytest.approx(KNOWN_GS_KT)

        expected_mph = KNOWN_GS_KT * KT_TO_MPH
        assert expected_mph == pytest.approx(529.36, abs=1.0)

        # The formatted string embeds the mph value (format_speed rounds to the nearest
        # whole unit) -- this is the end-to-end check that the original tool's unit bug
        # (mislabeling km/h math as mph) stays fixed.
        assert d["ground_speed"] == f"{round(expected_mph)} mph"
        assert abs(round(expected_mph) - 529) <= 2


class TestRenderJson:
    def test_round_trips_with_expected_count(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        text = output.render_json(aircraft_list, cfg.display.units)
        parsed = json.loads(text)
        assert isinstance(parsed, list)
        assert len(parsed) == len(aircraft_list)
        assert len(parsed) > 0

    def test_known_hex_present_in_json(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        text = output.render_json(aircraft_list, cfg.display.units)
        parsed = json.loads(text)
        assert any(entry["hex"] == KNOWN_HEX for entry in parsed)

    def test_empty_list_renders_empty_json_array(self, cfg):
        assert json.loads(output.render_json([], cfg.display.units)) == []


# --------------------------------------------------------------------------------------
# render_csv
# --------------------------------------------------------------------------------------


class TestRenderCsv:
    def test_header_and_row_count(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        text = output.render_csv(aircraft_list, cfg.display.units)
        reader = csv_reader(text)
        rows = list(reader)
        assert rows[0][0] == "hex"  # header row, first column
        assert len(rows) - 1 == len(aircraft_list)  # header + one row per aircraft

    def test_empty_list_still_has_fixed_header(self, cfg):
        text = output.render_csv([], cfg.display.units)
        rows = list(csv_reader(text))
        assert len(rows) == 1
        assert rows[0] == output._DICT_FIELDS


def csv_reader(text: str):
    import csv

    return csv.reader(io.StringIO(text))


# --------------------------------------------------------------------------------------
# render_table / render dispatch
# --------------------------------------------------------------------------------------


class TestRenderTable:
    def test_does_not_raise_and_contains_known_flight(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        text = output.render_table(aircraft_list, cfg.display.units)
        assert KNOWN_FLIGHT in text
        assert "FLIGHT" in text  # header present

    def test_empty_list_still_renders_header(self, cfg):
        text = output.render_table([], cfg.display.units)
        assert "FLIGHT" in text
        assert "\n" in text


class TestRenderDispatch:
    def test_dispatches_to_each_format(self, cfg, source):
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        assert output.render(aircraft_list, cfg.display.units, "table") == output.render_table(
            aircraft_list, cfg.display.units
        )
        assert output.render(aircraft_list, cfg.display.units, "json") == output.render_json(
            aircraft_list, cfg.display.units
        )
        assert output.render(aircraft_list, cfg.display.units, "csv") == output.render_csv(
            aircraft_list, cfg.display.units
        )

    def test_unknown_format_raises_value_error(self, cfg):
        with pytest.raises(ValueError):
            output.render([], cfg.display.units, "xml")


# --------------------------------------------------------------------------------------
# run_once
# --------------------------------------------------------------------------------------


class TestRunOnce:
    def test_success_returns_zero_and_prints_known_hex(self, cfg, source):
        out = io.StringIO()
        rc = output.run_once(cfg, source, "json", out=out)
        assert rc == 0
        assert KNOWN_HEX in out.getvalue()

    def test_error_returns_four_and_prints_nothing_to_out(self, cfg, capsys):
        out = io.StringIO()
        rc = output.run_once(cfg, _FailingSource(), "table", out=out)
        assert rc == 4
        assert out.getvalue() == ""
        captured = capsys.readouterr()
        assert "ERROR" in captured.err


# --------------------------------------------------------------------------------------
# run_watch
# --------------------------------------------------------------------------------------


class TestRunWatch:
    def test_two_iterations_no_sleep_prints_output_twice(self, cfg, source):
        out = io.StringIO()
        sleeps: list[float] = []
        rc = output.run_watch(
            cfg,
            source,
            "table",
            interval_s=1.0,
            iterations=2,
            out=out,
            sleep_fn=sleeps.append,
        )
        assert rc == 0
        text = out.getvalue()
        assert text.count("FLIGHT") == 2  # the table header, once per iteration
        assert sleeps == [1.0]  # slept once, between the two iterations, never after

    def test_all_iterations_erroring_returns_four(self, cfg):
        out = io.StringIO()
        rc = output.run_watch(
            cfg,
            _FailingSource(),
            "table",
            interval_s=0.0,
            iterations=2,
            out=out,
            sleep_fn=lambda s: None,
        )
        assert rc == 4
        assert out.getvalue() == "" or "FLIGHT" not in out.getvalue()


# --------------------------------------------------------------------------------------
# run_batch
# --------------------------------------------------------------------------------------


class TestRunBatch:
    def test_two_iterations_yields_at_least_one_line(self, cfg, source):
        lines = list(
            output.run_batch(cfg, source, diff_only=False, sleep_fn=lambda s: None, iterations=2)
        )
        assert len(lines) >= 1
        assert any(line.startswith("SEEN ") for line in lines)

    def test_diff_only_first_iteration_yields_nothing(self, cfg, source):
        lines = list(
            output.run_batch(cfg, source, diff_only=True, sleep_fn=lambda s: None, iterations=1)
        )
        assert lines == []

    def test_static_source_has_no_new_or_lost_on_second_iteration(self, cfg, source):
        # FileSource re-reads the same fixture every time, so the hex set never changes
        # across iterations -- no NEW/LOST lines should appear after the first iteration.
        lines = list(
            output.run_batch(cfg, source, diff_only=True, sleep_fn=lambda s: None, iterations=2)
        )
        assert lines == []

    def test_known_hex_appears_in_first_iteration_seen_lines(self, cfg, source):
        lines = list(
            output.run_batch(cfg, source, diff_only=False, sleep_fn=lambda s: None, iterations=1)
        )
        assert any(KNOWN_HEX in line for line in lines)

    def test_alert_line_for_the_emergency_aircraft(self, cfg, source):
        lines = list(
            output.run_batch(cfg, source, diff_only=False, sleep_fn=lambda s: None, iterations=1)
        )
        alerts_lines = [line for line in lines if line.startswith("ALERT ")]
        assert alerts_lines == [f"ALERT {EMERGENCY_HEX} emergency AAL911 12.7 mi"]

    def test_alert_is_not_repeated_while_the_level_holds(self, cfg, source):
        # The fixture never changes, so the emergency is a transition exactly once.
        lines = list(
            output.run_batch(cfg, source, diff_only=False, sleep_fn=lambda s: None, iterations=3)
        )
        assert sum(1 for line in lines if line.startswith("ALERT ")) == 1

    def test_alert_lines_are_suppressed_on_a_diff_only_first_iteration(self, cfg, source):
        lines = list(
            output.run_batch(cfg, source, diff_only=True, sleep_fn=lambda s: None, iterations=1)
        )
        assert lines == []

    def test_existing_line_formats_are_unchanged(self, cfg, source):
        """SEEN/NEW/LOST are a parsing contract for anything already piping this."""
        lines = list(
            output.run_batch(cfg, source, diff_only=False, sleep_fn=lambda s: None, iterations=1)
        )
        seen = [line for line in lines if line.startswith("SEEN ")]
        assert seen, "expected a SEEN line per tracked aircraft"
        for line in seen:
            assert len(line.split(" ")) >= 3  # SEEN <hex> <flight> <dist...>
        assert any(line.startswith(f"SEEN {KNOWN_HEX} {KNOWN_FLIGHT} ") for line in seen)


# --------------------------------------------------------------------------------------
# The shared pipeline helpers: the [filter] settings that used to be ignored outside the TUI
# --------------------------------------------------------------------------------------


class TestApplyFilters:
    """fetch_and_process() honors every [filter] key, not just the radius."""

    def test_hide_ground_drops_the_ground_vehicle(self, cfg, source):
        cfg.filter.hide_ground = True
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        assert GROUND_HEX not in {ac.hex for ac in aircraft_list}

    def test_include_nonicao_false_drops_the_tisb_address(self, cfg, source):
        cfg.filter.include_nonicao = False
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        assert not any(ac.hex.startswith("~") for ac in aircraft_list)
        assert KNOWN_HEX in {ac.hex for ac in aircraft_list}

    def test_altitude_band_drops_low_and_high_traffic(self, cfg, source):
        cfg.filter.min_alt_ft = 5000
        cfg.filter.max_alt_ft = 20000
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        hexes = {ac.hex for ac in aircraft_list}
        assert KNOWN_HEX not in hexes  # 35,000ft, above the band
        assert EMERGENCY_HEX not in hexes  # 3,200ft, below it
        assert "aa1122" in hexes  # 8,000ft, inside it

    def test_aircraft_without_altitude_survives_the_band(self, cfg, source):
        # The ground vehicle reports alt_baro="ground" (no numeric altitude); an unknown
        # altitude is not a reason to hide an aircraft.
        cfg.filter.min_alt_ft = 5000
        cfg.filter.max_alt_ft = 20000
        aircraft_list, err = output.fetch_and_process(source, cfg)
        assert err is None
        assert GROUND_HEX in {ac.hex for ac in aircraft_list}


def test_output_module_never_imports_curses(tmp_path):
    """The whole reason this module exists: --headless must work with no terminal at all.

    Checked in a fresh interpreter (cwd outside the repo so the adsbtui.py shim at the
    repository root does not shadow the package) because an earlier test importing curses
    would make an in-process sys.modules check meaningless.
    """
    src_root = Path(output.__file__).resolve().parents[1]
    code = "import sys, adsbtui.output; sys.exit(1 if 'curses' in sys.modules else 0)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": str(src_root)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"adsbtui.output pulled in curses: {result.stderr}"


# --------------------------------------------------------------------------------------
# HeadlessRunner: the full service pipeline
# --------------------------------------------------------------------------------------


class _RecordingDispatcher:
    """Stands in for dispatch.Dispatcher, recording events instead of delivering them.

    This is what keeps the headless tests from ever beeping, POSTing a webhook, or
    exec'ing a command -- the real Dispatcher's channels are never constructed at all.
    """

    def __init__(self) -> None:
        self.events: list = []

    def maybe_fire(self, event) -> bool:
        self.events.append(event)
        return True

    def names(self) -> list[tuple[str, str]]:
        return [(e.hex, e.event_name) for e in self.events]


class _ScriptedSource:
    """A Source that returns a canned raw payload per fetch, repeating the last one."""

    def __init__(self, payloads: list[dict]) -> None:
        self._payloads = payloads
        self.calls = 0

    def fetch(self, timeout: float) -> dict:
        payload = self._payloads[min(self.calls, len(self._payloads) - 1)]
        self.calls += 1
        return payload


def _fixture_payload() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _empty_payload() -> dict:
    return {"now": 1700000005.0, "messages": 123999, "aircraft": []}


def _runner(cfg, source=None, dispatcher=None):
    return output.HeadlessRunner(
        cfg,
        source if source is not None else make_source(),
        dispatcher=dispatcher if dispatcher is not None else _RecordingDispatcher(),
    )


class TestHeadlessRunnerTick:
    def test_tick_tracks_and_grades_aircraft(self, cfg):
        result = _runner(cfg).tick()
        assert result.error is None
        by_hex = {ac.hex: ac for ac in result.aircraft}
        assert KNOWN_HEX in by_hex
        # tracker.update_tracks stamped the cross-tick bookkeeping the TUI relies on.
        assert by_hex[KNOWN_HEX].first_seen is not None
        assert by_hex[KNOWN_HEX].is_new is True
        assert by_hex[EMERGENCY_HEX].alert_level == AlertLevel.EMERGENCY

    def test_source_error_is_returned_not_raised(self, cfg):
        result = _runner(cfg, source=_FailingSource()).tick()
        assert result.aircraft == []
        assert result.events == []
        assert "simulated failure" in str(result.error)

    def test_emergency_aircraft_reaches_the_dispatcher(self, cfg):
        dispatcher = _RecordingDispatcher()
        result = _runner(cfg, dispatcher=dispatcher).tick()
        assert (EMERGENCY_HEX, "emergency") in dispatcher.names()
        assert (EMERGENCY_HEX, "emergency") in [(e.hex, e.event_name) for e in result.events]

    def test_an_unchanged_level_does_not_re_alert_on_the_next_tick(self, cfg):
        dispatcher = _RecordingDispatcher()
        runner = _runner(cfg, dispatcher=dispatcher)
        runner.tick()
        fired_once = len(dispatcher.events)
        second = runner.tick()
        # The fixture never changes, so no aircraft transitions to a new level.
        assert second.events == []
        assert len(dispatcher.events) == fired_once

    def test_disabled_event_name_suppresses_dispatch(self, cfg):
        cfg.alerts.events = ["proximity", "cpa"]  # emergency deliberately not enabled
        dispatcher = _RecordingDispatcher()
        _runner(cfg, dispatcher=dispatcher).tick()
        assert dispatcher.names() == []

    def test_alert_events_are_logged_at_info(self, cfg, caplog):
        caplog.set_level(logging.INFO, logger="adsbtui")
        _runner(cfg).tick()
        assert any(EMERGENCY_HEX in record.getMessage() for record in caplog.records)


class TestHeadlessRunnerWatchlist:
    def test_watchlist_match_is_detected(self, cfg, tmp_path):
        path = tmp_path / "watchlist.txt"
        path.write_text("# a comment\ncall:UAL*\n", encoding="utf-8")
        cfg.watchlist.path = str(path)

        result = _runner(cfg).tick()
        watched = {ac.hex for ac in result.aircraft if ac.is_watched}
        assert watched == {KNOWN_HEX}

    def test_watchlist_hit_fires_its_own_event(self, cfg, tmp_path):
        # GROUND_HEX grades AlertLevel.NONE, so nothing but the watchlist can raise it.
        path = tmp_path / "watchlist.txt"
        path.write_text(f"hex:{GROUND_HEX}\n", encoding="utf-8")
        cfg.watchlist.path = str(path)

        dispatcher = _RecordingDispatcher()
        _runner(cfg, dispatcher=dispatcher).tick()
        assert (GROUND_HEX, "watchlist") in dispatcher.names()

    def test_no_watchlist_file_means_no_watched_aircraft(self, cfg, tmp_path):
        cfg.watchlist.path = str(tmp_path / "does-not-exist.txt")
        result = _runner(cfg).tick()
        assert not any(ac.is_watched for ac in result.aircraft)


class TestHeadlessRunnerHistory:
    def _closing_runner(self, cfg, db_path):
        cfg.history.db = str(db_path)
        # A negative linger window closes every track the moment it stops being reported,
        # so the test does not depend on the wall clock advancing between two ticks.
        cfg.display.linger_s = -1.0
        source = _ScriptedSource([_fixture_payload(), _empty_payload()])
        return _runner(cfg, source=source)

    def test_closed_tracks_are_written_to_the_database(self, cfg, tmp_path):
        db_path = tmp_path / "sightings.db"
        runner = self._closing_runner(cfg, db_path)

        first = runner.tick()
        assert first.closed_tracks == 0
        second = runner.tick()
        assert second.aircraft == []
        assert second.closed_tracks == len(first.aircraft)

        conn = sqlite3.connect(str(db_path))
        try:
            rows = dict(conn.execute("SELECT hex, had_emergency FROM sightings").fetchall())
        finally:
            conn.close()
        assert KNOWN_HEX in rows
        assert rows[EMERGENCY_HEX] == 1  # the 7700 squawk was remembered on the row

    def test_summary_stats_are_accumulated(self, cfg, tmp_path):
        db_path = tmp_path / "sightings.db"
        runner = self._closing_runner(cfg, db_path)
        runner.tick()
        runner.tick()

        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT min_distance_mi, max_altitude_ft, max_gs_kt, callsign, squawks "
                "FROM sightings WHERE hex = ?",
                (KNOWN_HEX,),
            ).fetchone()
        finally:
            conn.close()
        min_distance, max_alt, max_gs, callsign, squawks = row
        assert min_distance == pytest.approx(5.3, abs=0.5)
        assert max_alt == pytest.approx(35000.0)
        assert max_gs == pytest.approx(KNOWN_GS_KT)
        assert callsign == KNOWN_FLIGHT
        assert squawks == "1234"

    def test_no_history_db_configured_writes_nothing(self, cfg, tmp_path):
        cfg.history.db = ""
        cfg.display.linger_s = -1.0
        source = _ScriptedSource([_fixture_payload(), _empty_payload()])
        runner = _runner(cfg, source=source)
        runner.tick()
        assert runner.tick().closed_tracks == 0
        assert list(tmp_path.iterdir()) == []


# --------------------------------------------------------------------------------------
# run_headless
# --------------------------------------------------------------------------------------


class TestRunHeadless:
    def test_stops_after_n_calls_and_never_prints(self, cfg, source, capsys):
        calls = {"n": 0}

        def stop_predicate():
            calls["n"] += 1
            return calls["n"] > 3

        sleeps: list[float] = []
        rc = output.run_headless(cfg, source, stop_predicate, sleep_fn=sleeps.append)
        assert rc == 0
        assert calls["n"] == 4  # 3 loop bodies executed, 4th check stops the loop
        assert len(sleeps) == 3
        assert sleeps == [cfg.source.refresh_s] * 3
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_immediate_stop_does_no_work(self, cfg, source):
        sleeps: list[float] = []
        rc = output.run_headless(cfg, source, lambda: True, sleep_fn=sleeps.append)
        assert rc == 0
        assert sleeps == []

    def test_logs_a_cycle_summary_and_each_alert(self, cfg, source, caplog):
        caplog.set_level(logging.INFO, logger="adsbtui")
        calls = {"n": 0}

        def stop_predicate():
            calls["n"] += 1
            return calls["n"] > 1

        output.run_headless(cfg, source, stop_predicate, sleep_fn=lambda _s: None)
        messages = [record.getMessage() for record in caplog.records]
        assert any(message.startswith("cycle: ") for message in messages)
        assert any(message.startswith("alert emergency:") for message in messages)

    def test_writes_history_for_tracks_that_close(self, cfg, tmp_path):
        cfg.history.db = str(tmp_path / "sightings.db")
        cfg.display.linger_s = -1.0
        source = _ScriptedSource([_fixture_payload(), _empty_payload()])

        calls = {"n": 0}

        def stop_predicate():
            calls["n"] += 1
            return calls["n"] > 2

        assert output.run_headless(cfg, source, stop_predicate, sleep_fn=lambda _s: None) == 0

        conn = sqlite3.connect(cfg.history.db)
        try:
            (count,) = conn.execute("SELECT COUNT(*) FROM sightings").fetchone()
        finally:
            conn.close()
        assert count > 0

    def test_a_failing_source_does_not_stop_the_loop(self, cfg, capsys):
        calls = {"n": 0}

        def stop_predicate():
            calls["n"] += 1
            return calls["n"] > 2

        sleeps: list[float] = []
        rc = output.run_headless(cfg, _FailingSource(), stop_predicate, sleep_fn=sleeps.append)
        assert rc == 0
        assert len(sleeps) == 2
        assert capsys.readouterr().out == ""

    def test_an_unexpected_exception_does_not_end_the_loop(self, cfg, caplog):
        """A malformed snapshot is not a reason for the service to exit."""

        class _ExplodingSource:
            def fetch(self, timeout: float) -> dict:
                raise ValueError("boom")

        calls = {"n": 0}

        def stop_predicate():
            calls["n"] += 1
            return calls["n"] > 2

        sleeps: list[float] = []
        caplog.set_level(logging.ERROR, logger="adsbtui")
        rc = output.run_headless(cfg, _ExplodingSource(), stop_predicate, sleep_fn=sleeps.append)
        assert rc == 0
        assert len(sleeps) == 2
        assert any("unexpected error" in record.getMessage() for record in caplog.records)
