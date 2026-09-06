"""Tests for adsbtui.output: the non-curses table/json/csv formatters and the
--once/--watch/--headless/--batch runners.

Uses a real FileSource against tests/fixtures/aircraft.json and a real Config built
through adsbtui.config.build_config() (allow_null_home=True as the escape hatch, paired
with real home coordinates so distance/bearing/CPA actually compute).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from adsbtui import output
from adsbtui.config import build_config
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


def make_cfg():
    return build_config(
        toml_dict={},
        env={},
        cli={
            "source.url": str(FIXTURE_PATH),
            "home.lat": HOME_LAT,
            "home.lon": HOME_LON,
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
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_immediate_stop_does_no_work(self, cfg, source):
        sleeps: list[float] = []
        rc = output.run_headless(cfg, source, lambda: True, sleep_fn=sleeps.append)
        assert rc == 0
        assert sleeps == []


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
