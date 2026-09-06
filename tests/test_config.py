"""Tests for adsbtui.config: schema defaults, TOML/env/CLI layering, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from adsbtui.config import (
    Config,
    ConfigError,
    build_config,
    cli_overrides,
    find_config_path,
    load_config,
    load_toml,
    validate,
)


def _base_toml(lat: float = 1.0, lon: float = 1.0) -> dict:
    """A minimal valid TOML dict: a source URL plus a real (non-placeholder) home."""
    return {
        "source": {"url": "http://host/aircraft.json"},
        "home": {"lat": lat, "lon": lon},
    }


class TestDefaults:
    def test_defaults_only_config_passes_with_allow_null_home(self):
        cfg = build_config(
            {"source": {"url": "http://localhost:8080/data/aircraft.json"}}, allow_null_home=True
        )
        assert isinstance(cfg, Config)
        assert cfg.home.lat == 0.0
        assert cfg.home.lon == 0.0
        assert cfg.source.refresh_s == 5.0
        assert cfg.filter.radius == 15.0
        assert cfg.display.units == "imperial"
        assert cfg.alerts.squawks == [7500, 7600, 7700]

    def test_defaults_only_without_allow_null_home_rejected(self):
        with pytest.raises(ConfigError, match="lat"):
            build_config({"source": {"url": "http://localhost/aircraft.json"}})

    def test_missing_url_rejected_even_with_allow_null_home(self):
        with pytest.raises(ConfigError, match="source.url"):
            build_config({}, allow_null_home=True)


class TestLayeringPrecedence:
    def test_toml_env_cli_precedence(self, tmp_path: Path):
        toml_path = tmp_path / "adsb-tui.toml"
        toml_path.write_text(
            """
            [source]
            url = "http://toml-host/aircraft.json"
            refresh_s = 10.0

            [home]
            lat = 10.0
            lon = 20.0
            """
        )
        toml_dict = load_toml(str(toml_path))

        # env overrides the TOML refresh_s and home.lat...
        env = {
            "source.refresh_s": "7.0",
            "home.lat": "30.0",
        }
        # ...but cli wins over both env and TOML for home.lat, and leaves refresh_s
        # (set only by env) and url (set only by TOML) alone.
        cli = {
            "home.lat": "40.0",
        }

        cfg = build_config(toml_dict, env=env, cli=cli)

        assert cfg.source.url == "http://toml-host/aircraft.json"  # from TOML only
        assert cfg.source.refresh_s == 7.0  # env overrides TOML
        assert cfg.home.lat == 40.0  # cli overrides env and TOML
        assert cfg.home.lon == 20.0  # untouched, from TOML

    def test_cli_none_values_do_not_override(self, tmp_path: Path):
        toml_dict = _base_toml(lat=5.0, lon=5.0)
        cfg = build_config(toml_dict, cli={"home.lat": None})
        assert cfg.home.lat == 5.0


class TestValidation:
    def test_invalid_lat_raises_with_lat_in_message(self):
        with pytest.raises(ConfigError, match="lat"):
            build_config(_base_toml(lat=200.0, lon=0.0))

    def test_invalid_lon_raises_with_lon_in_message(self):
        with pytest.raises(ConfigError, match="lon"):
            build_config(_base_toml(lat=10.0, lon=-200.0))

    def test_nonzero_home_out_of_range_rejected_even_with_allow_null_home(self):
        # allow_null_home only forgives the exact (0.0, 0.0) placeholder, not any other
        # out-of-range value.
        with pytest.raises(ConfigError, match="lat"):
            build_config(_base_toml(lat=500.0, lon=0.0), allow_null_home=True)

    def test_nonzero_valid_home_does_not_need_allow_null_home(self):
        cfg = build_config(_base_toml(lat=37.7749, lon=-122.4194))
        assert cfg.home.lat == pytest.approx(37.7749)

    @pytest.mark.parametrize(
        "section,key,value",
        [
            ("source", "refresh_s", -1.0),
            ("source", "timeout_s", 0.0),
            ("filter", "radius", -5.0),
            ("filter", "proximity", 0.0),
        ],
    )
    def test_non_positive_fields_rejected(self, section, key, value):
        toml_dict = {
            "source": {"url": "http://host/aircraft.json"},
            "home": {"lat": 10.0, "lon": 10.0},
        }
        toml_dict.setdefault(section, {})[key] = value
        with pytest.raises(ConfigError, match=f"{section}.{key}"):
            build_config(toml_dict)

    @pytest.mark.parametrize(
        "section,key,value",
        [
            ("display", "units", "furlongs"),
            ("display", "borders", "fancy"),
            ("display", "color", "sometimes"),
            ("alerts", "webhook_format", "yaml"),
        ],
    )
    def test_bad_enum_values_rejected(self, section, key, value):
        toml_dict = {
            "source": {"url": "http://host/aircraft.json"},
            "home": {"lat": 10.0, "lon": 10.0},
            section: {key: value},
        }
        with pytest.raises(ConfigError, match=f"{section}.{key}"):
            build_config(toml_dict)

    def test_validate_can_be_called_directly(self):
        cfg = Config()
        cfg.source.url = "http://host/aircraft.json"
        cfg.home.lat = 10.0
        cfg.home.lon = 10.0
        validate(cfg)  # should not raise

        cfg.home.lat = 999.0
        with pytest.raises(ConfigError, match="home.lat"):
            validate(cfg)


class TestEnvCoercion:
    @pytest.mark.parametrize("spelling", ["1", "true", "True", "TRUE", "yes", "Yes"])
    def test_env_bool_true_spellings(self, spelling):
        toml_dict = _base_toml()
        cfg = build_config(toml_dict, env={"filter.hide_ground": spelling})
        assert cfg.filter.hide_ground is True

    @pytest.mark.parametrize("spelling", ["0", "false", "False", "FALSE", "no", "No"])
    def test_env_bool_false_spellings(self, spelling):
        toml_dict = _base_toml()
        cfg = build_config(
            toml_dict, env={"filter.hide_ground": "true", "watchlist.pin_top": spelling}
        )
        assert cfg.watchlist.pin_top is False

    def test_env_bool_garbage_raises(self):
        toml_dict = _base_toml()
        with pytest.raises(ConfigError, match="filter.hide_ground"):
            build_config(toml_dict, env={"filter.hide_ground": "maybe"})

    def test_env_list_int_parsing_for_squawks(self):
        toml_dict = _base_toml()
        cfg = build_config(toml_dict, env={"alerts.squawks": "7500,7600,7700"})
        assert cfg.alerts.squawks == [7500, 7600, 7700]

    def test_env_list_str_parsing_for_columns(self):
        toml_dict = _base_toml()
        cfg = build_config(toml_dict, env={"display.columns": "flight,alt,gs"})
        assert cfg.display.columns == ["flight", "alt", "gs"]

    def test_env_float_and_int_coercion(self):
        toml_dict = _base_toml()
        cfg = build_config(
            toml_dict,
            env={"alerts.cpa_distance": "2.5", "filter.min_alt_ft": "1000"},
        )
        assert cfg.alerts.cpa_distance == 2.5
        assert cfg.filter.min_alt_ft == 1000


class TestUnknownKeys:
    def test_unknown_section_in_toml_rejected(self):
        with pytest.raises(ConfigError, match="unknown config section"):
            build_config({"bogus": {"foo": "bar"}})

    def test_unknown_key_in_toml_rejected(self):
        with pytest.raises(ConfigError, match="unknown config key"):
            build_config({"source": {"url": "http://host/aircraft.json", "nope": 1}})

    def test_unknown_section_in_env_rejected(self):
        toml_dict = _base_toml()
        with pytest.raises(ConfigError, match="unknown config section"):
            build_config(toml_dict, env={"bogus.foo": "bar"})


class TestLoadToml:
    def test_none_path_returns_empty_dict(self):
        assert load_toml(None) == {}

    def test_missing_file_raises_configerror(self, tmp_path: Path):
        with pytest.raises(ConfigError):
            load_toml(str(tmp_path / "does-not-exist.toml"))

    def test_malformed_toml_raises_configerror(self, tmp_path: Path):
        bad = tmp_path / "bad.toml"
        bad.write_text("this is not [valid toml")
        with pytest.raises(ConfigError):
            load_toml(str(bad))

    def test_well_formed_toml_round_trips(self, tmp_path: Path):
        good = tmp_path / "good.toml"
        good.write_text('[source]\nurl = "http://host/aircraft.json"\n')
        result = load_toml(str(good))
        assert result == {"source": {"url": "http://host/aircraft.json"}}


class TestFindConfigPath:
    def test_explicit_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADSBTUI_CONFIG", str(tmp_path / "env.toml"))
        assert find_config_path(str(tmp_path / "explicit.toml")) == str(tmp_path / "explicit.toml")

    def test_env_var_used_when_no_explicit(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADSBTUI_CONFIG", str(tmp_path / "env.toml"))
        assert find_config_path(None) == str(tmp_path / "env.toml")

    def test_cwd_file_used_when_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "adsb-tui.toml").write_text('[source]\nurl = "x"\n')
        assert find_config_path(None) == "adsb-tui.toml"

    def test_xdg_config_used_when_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # empty dir, no ./adsb-tui.toml
        xdg = tmp_path / "xdgconf"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
        config_dir = xdg / "adsbtui"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('[source]\nurl = "x"\n')
        assert find_config_path(None) == str(config_dir / "config.toml")

    def test_none_when_nothing_found(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-such-xdg-dir"))
        assert find_config_path(None) is None


class TestCliOverrides:
    def test_only_passed_flags_included(self):
        from adsbtui.config import build_argparser

        parser = build_argparser()
        args = parser.parse_args(["--url", "http://host/aircraft.json", "--lat", "12.5"])
        overrides = cli_overrides(args)
        assert overrides["source.url"] == "http://host/aircraft.json"
        assert overrides["home.lat"] == 12.5
        assert "home.lon" not in overrides
        assert "filter.radius" not in overrides

    def test_no_color_flag_maps_to_display_color_never(self):
        from adsbtui.config import build_argparser

        parser = build_argparser()
        args = parser.parse_args(["--no-color"])
        overrides = cli_overrides(args)
        assert overrides["display.color"] == "never"

    def test_debug_flag_forces_log_level_debug(self):
        from adsbtui.config import build_argparser

        parser = build_argparser()
        args = parser.parse_args(["--debug"])
        overrides = cli_overrides(args)
        assert overrides["logging.level"] == "DEBUG"


class TestLoadConfig:
    def test_full_precedence_through_load_config(self, tmp_path, monkeypatch):
        toml_path = tmp_path / "adsb-tui.toml"
        toml_path.write_text(
            """
            [source]
            url = "http://toml-host/aircraft.json"
            refresh_s = 10.0

            [home]
            lat = 10.0
            lon = 20.0
            """
        )
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.setenv("ADSBTUI_SOURCE_REFRESH_S", "7.0")
        monkeypatch.setenv("ADSBTUI_HOME_LAT", "30.0")

        cfg = load_config(["--config", str(toml_path), "--lat", "40.0"])

        assert cfg.source.url == "http://toml-host/aircraft.json"
        assert cfg.source.refresh_s == 7.0  # env beats TOML
        assert cfg.home.lat == 40.0  # cli beats env and TOML
        assert cfg.home.lon == 20.0  # from TOML, untouched
        assert cfg._config_path == str(toml_path)
        assert cfg._args.lat == 40.0

    def test_load_config_with_no_file_and_valid_cli_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)  # no adsb-tui.toml here
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-such-xdg-dir"))
        cfg = load_config(
            ["--url", "http://host/aircraft.json", "--lat", "37.7749", "--lon", "-122.4194"]
        )
        assert cfg.source.url == "http://host/aircraft.json"
        assert cfg.home.lat == pytest.approx(37.7749)
        assert cfg._config_path is None

    def test_load_config_missing_url_raises(self, tmp_path, monkeypatch):
        monkeypatch.delenv("ADSBTUI_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "no-such-xdg-dir"))
        with pytest.raises(ConfigError):
            load_config(["--lat", "1.0", "--lon", "1.0"])
