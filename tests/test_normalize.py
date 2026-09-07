"""Tests for adsbtui.normalize."""

from __future__ import annotations

from adsbtui.normalize import (
    parse_aircraft,
    parse_snapshot,
    sanitize,
)

# --------------------------------------------------------------------------- sanitize()


def test_sanitize_strips_control_chars_and_nul():
    assert sanitize("UAL\x00123\x01\x02  ") == "UAL123"


def test_sanitize_strips_whitespace_padding():
    assert sanitize("UAL123  ") == "UAL123"


def test_sanitize_empty_after_cleaning_is_none():
    assert sanitize("\x00\x01\x02") is None
    assert sanitize("   ") is None
    assert sanitize("") is None


def test_sanitize_non_string_is_none():
    assert sanitize(None) is None
    assert sanitize(1234) is None
    assert sanitize(["a", "b"]) is None


def test_sanitize_truncates_to_max_len():
    assert sanitize("ABCDEFGHIJKLMNOP", max_len=5) == "ABCDE"


def test_sanitize_never_raises_on_surrogates_and_wide_chars():
    # lone surrogate -- not valid text but must not raise; may or may not survive cleaning
    surrogate = "AB\ud800CD"
    result = sanitize(surrogate)
    assert result is None or isinstance(result, str)

    # wide (CJK) chars are printable and should survive
    assert sanitize("N123中文") == "N123中文"


# --------------------------------------------------------------------------- parse_snapshot()


def test_parse_snapshot_normal():
    raw = {"now": 1700000000.0, "messages": 123456, "aircraft": [{"hex": "abc123"}]}
    snap = parse_snapshot(raw, fetched_at=1700000005.0)
    assert snap.now == 1700000000.0
    assert snap.messages == 123456
    assert snap.raw_aircraft == [{"hex": "abc123"}]
    assert snap.fetched_at == 1700000005.0


def test_parse_snapshot_falls_back_to_fetched_at_when_now_missing():
    snap = parse_snapshot({}, fetched_at=1700000005.0)
    assert snap.now == 1700000005.0
    assert snap.messages is None
    assert snap.raw_aircraft == []


def test_parse_snapshot_falls_back_when_now_is_not_a_number():
    snap = parse_snapshot({"now": "not-a-number"}, fetched_at=42.0)
    assert snap.now == 42.0


def test_parse_snapshot_defaults_aircraft_list_when_malformed():
    snap = parse_snapshot({"aircraft": "not-a-list"}, fetched_at=1.0)
    assert snap.raw_aircraft == []


# --------------------------------------------------------------------------- parse_aircraft()


def test_parse_aircraft_normal_airborne_record():
    raw = {
        "hex": "a1b2c3",
        "type": "adsb_icao",
        "flight": "UAL123  ",
        "alt_baro": 35000,
        "alt_geom": 35250,
        "gs": 460.0,
        "track": 270.5,
        "baro_rate": -64,
        "geom_rate": -64,
        "squawk": "1234",
        "emergency": "none",
        "category": "A3",
        "nav_altitude_mcp": 35000,
        "lat": 37.85,
        "lon": -122.4,
        "seen_pos": 0.4,
        "seen": 0.1,
        "rssi": -18.2,
        "messages": 5421,
    }
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.hex == "a1b2c3"
    assert ac.is_icao is True
    assert ac.flight == "UAL123"
    assert ac.on_ground is False
    assert ac.altitude_ft == 35000.0
    assert ac.geom_altitude_ft == 35250.0
    assert ac.ground_speed_kt == 460.0
    assert ac.track_deg == 270.5
    assert ac.baro_rate_fpm == -64.0
    assert ac.geom_rate_fpm == -64.0
    assert ac.squawk == "1234"
    assert ac.emergency is None  # "none" means no emergency
    assert ac.category == "A3"
    assert ac.nav_altitude_mcp == 35000.0
    assert ac.lat == 37.85
    assert ac.lon == -122.4
    assert ac.seen_pos_s == 0.4
    assert ac.seen_s == 0.1
    assert ac.rssi == -18.2
    assert ac.messages == 5421
    assert ac.source_type == "adsb_icao"


def test_parse_aircraft_ground_record():
    raw = {
        "hex": "a4d5e6",
        "type": "adsb_icao",
        "flight": "N12345  ",
        "alt_baro": "ground",
        "gs": 12.0,
        "track": 90.0,
        "lat": 37.62,
        "lon": -122.38,
        "squawk": "1200",
        "emergency": "none",
    }
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.on_ground is True
    assert ac.altitude_ft is None
    assert ac.ground_speed_kt == 12.0


def test_parse_aircraft_tilde_prefixed_is_not_icao():
    raw = {
        "hex": "~a55501",
        "type": "tisb_other",
        "flight": "",
        "alt_baro": 4500,
        "gs": 95.0,
        "track": 180.0,
        "lat": 37.9,
        "lon": -122.5,
    }
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.hex == "~a55501"
    assert ac.is_icao is False
    assert ac.flight is None  # empty string -> no callsign


def test_parse_aircraft_zero_speed_and_zero_altitude_are_kept_not_none():
    raw = {"hex": "b00001", "alt_baro": 0, "gs": 0.0, "track": 0.0}
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.ground_speed_kt == 0.0
    assert ac.ground_speed_kt is not None
    assert ac.altitude_ft == 0.0
    assert ac.altitude_ft is not None
    assert ac.on_ground is False
    assert ac.track_deg == 0.0


def test_parse_aircraft_null_speed_and_track_mlat_record():
    raw = {
        "hex": "aa1122",
        "type": "mlat",
        "flight": "SKW4455 ",
        "alt_baro": 8000,
        "gs": None,
        "track": None,
        "lat": 37.7,
        "lon": -122.2,
    }
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.ground_speed_kt is None
    assert ac.track_deg is None
    assert ac.source_type == "mlat"


def test_parse_aircraft_missing_lat_lon():
    raw = {"hex": "c00099", "type": "mode_s", "alt_baro": 1200}
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.lat is None
    assert ac.lon is None


def test_parse_aircraft_malformed_hex_is_int_returns_none():
    raw = {"hex": 123456, "flight": "XX"}
    assert parse_aircraft(raw) is None


def test_parse_aircraft_missing_hex_returns_none():
    raw = {"flight": "NOHEX123"}
    assert parse_aircraft(raw) is None


def test_parse_aircraft_empty_dict_returns_none():
    assert parse_aircraft({}) is None


def test_parse_aircraft_control_char_flight_is_sanitized():
    raw = {"hex": "d00001", "flight": "UAL\x00123\x07  "}
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.flight == "UAL123"


def test_parse_aircraft_military_db_flags_and_enrichment_fields():
    raw = {
        "hex": "adf7c8",
        "type": "adsb_icao",
        "flight": "RCH245  ",
        "r": "12-1234",
        "t": "C17",
        "desc": "BOEING C-17A GLOBEMASTER III",
        "ownOp": "UNITED STATES AIR FORCE",
        "year": "2012",
        "dbFlags": 1,
        "alt_baro": 22000,
        "gs": 310.0,
    }
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.registration == "12-1234"
    assert ac.type_code == "C17"
    assert ac.type_desc == "BOEING C-17A GLOBEMASTER III"
    assert ac.owner_operator == "UNITED STATES AIR FORCE"
    assert ac.year == "2012"
    assert ac.db_flags == 1
    assert ac.is_military is True


def test_parse_aircraft_missing_db_flags_defaults_to_zero():
    ac = parse_aircraft({"hex": "e00001"})
    assert ac is not None
    assert ac.db_flags == 0
    assert ac.is_military is False


def test_parse_aircraft_extra_holds_shallow_copy_of_raw():
    raw = {"hex": "f00001", "some_future_field": "value"}
    ac = parse_aircraft(raw)
    assert ac is not None
    assert ac.extra.get("some_future_field") == "value"
    assert ac.extra.get("hex") == "f00001"


def test_parse_aircraft_emergency_variants():
    assert parse_aircraft({"hex": "a1", "emergency": "none"}).emergency is None
    assert parse_aircraft({"hex": "a2", "emergency": ""}).emergency is None
    assert parse_aircraft({"hex": "a3"}).emergency is None
    assert parse_aircraft({"hex": "a4", "emergency": "general"}).emergency == "general"
