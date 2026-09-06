"""Configuration: schema, layered loading (TOML < env < CLI), and validation.

The schema is a set of small mutable dataclasses, one per TOML section (SourceConfig,
HomeConfig, FilterConfig, DisplayConfig, AlertsConfig, WatchlistConfig, RegistryConfig,
HistoryConfig, LoggingConfig), bundled into a top-level Config dataclass. They are
deliberately mutable (not frozen) because a few settings -- sort order, display units --
can change live at runtime once the UI is up.

Precedence when building a Config is CLI flag > environment variable > TOML file > the
default baked into the dataclass field. build_config() takes the already-parsed TOML dict
plus flat "section.key" -> value dicts for env and CLI and layers them in that order.

load_config() is the top-level entry point most callers want: it builds the argparser,
parses argv, reads os.environ, locates and loads the TOML file, layers everything through
build_config()/validate(), and returns the resulting Config. It also stamps two extra
attributes onto the returned Config -- config._args (the parsed argparse.Namespace) and
config._config_path (the resolved TOML path, or None) -- so a caller that wants them
does not have to re-parse anything itself.
"""

from __future__ import annotations

import argparse
import os
import tomllib
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised for any configuration problem: a malformed file, or a value that fails
    validate(). Messages always name the offending "section.key" and say why it is
    rejected, e.g. "home.lat must be between -90 and 90, got 200.0"."""


# --------------------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------------------


@dataclass
class SourceConfig:
    url: str = ""
    refresh_s: float = 5.0
    timeout_s: float = 5.0
    stale_s: float = 30.0
    backoff_max_s: float = 30.0
    max_bytes: int = 8_000_000


@dataclass
class HomeConfig:
    lat: float = 0.0
    lon: float = 0.0


@dataclass
class FilterConfig:
    radius: float = 15.0
    proximity: float = 5.0
    hide_ground: bool = False
    include_nonicao: bool = True
    min_alt_ft: int = 0
    max_alt_ft: int = 60000


@dataclass
class DisplayConfig:
    units: str = "imperial"
    theme: str = "default"
    color: str = "auto"
    columns: list[str] = field(
        default_factory=lambda: [
            "flight",
            "reg",
            "type",
            "alt",
            "vs",
            "gs",
            "dist",
            "brg",
            "cpa",
            "owner",
            "flags",
            "age",
            "alert",
        ]
    )
    owner_width: int = 30
    borders: str = "unicode"
    density: str = "normal"
    sort_key: str = "distance"
    sort_reverse: bool = False
    stale_after_s: float = 15.0
    linger_s: float = 30.0
    status_bar: bool = True
    vs_threshold_fpm: float = 256.0


@dataclass
class AlertsConfig:
    emergency: bool = True
    squawks: list[int] = field(default_factory=lambda: [7500, 7600, 7700])
    emergency_ignore_radius: bool = True
    cpa_enabled: bool = True
    cpa_distance: float = 1.0
    cpa_horizon_s: float = 600.0
    cpa_min_gs_kt: float = 30.0
    events: list[str] = field(
        default_factory=lambda: ["proximity", "cpa", "watchlist", "emergency"]
    )
    bell: bool = True
    desktop: bool = False
    webhook_url: str = ""
    webhook_format: str = "auto"
    command: str = ""
    cooldown_s: float = 600.0
    quiet_hours: str = ""


@dataclass
class WatchlistConfig:
    path: str = "~/.config/adsbtui/watchlist.txt"
    pin_top: bool = True
    ignore_radius: bool = True


@dataclass
class RegistryConfig:
    path: str = ""


@dataclass
class HistoryConfig:
    db: str = ""
    retention_days: int = 365


@dataclass
class LoggingConfig:
    file: str = "~/.local/state/adsbtui/adsbtui.log"
    level: str = "INFO"
    max_bytes: int = 1_048_576
    backup_count: int = 3
    redact_home: bool = True


@dataclass
class Config:
    source: SourceConfig = field(default_factory=SourceConfig)
    home: HomeConfig = field(default_factory=HomeConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    watchlist: WatchlistConfig = field(default_factory=WatchlistConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    history: HistoryConfig = field(default_factory=HistoryConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    # Populated by load_config(); not part of the TOML/env/CLI schema itself.
    _args: argparse.Namespace | None = field(default=None, repr=False, compare=False)
    _config_path: str | None = field(default=None, repr=False, compare=False)


#: section name -> dataclass type, driving both the layering logic and type coercion.
_SECTIONS: dict[str, type] = {
    "source": SourceConfig,
    "home": HomeConfig,
    "filter": FilterConfig,
    "display": DisplayConfig,
    "alerts": AlertsConfig,
    "watchlist": WatchlistConfig,
    "registry": RegistryConfig,
    "history": HistoryConfig,
    "logging": LoggingConfig,
}


# --------------------------------------------------------------------------------------
# Config file discovery and TOML loading
# --------------------------------------------------------------------------------------


def find_config_path(explicit: str | None = None) -> str | None:
    """Resolve which TOML config file to use, or None if none is found.

    Precedence: `explicit` argument, then the ADSBTUI_CONFIG env var, then ./adsb-tui.toml
    (if it exists in the current directory), then $XDG_CONFIG_HOME/adsbtui/config.toml
    (defaulting XDG_CONFIG_HOME to ~/.config if unset), if that exists. A missing file at
    every candidate is not an error -- it just means defaults/env/CLI apply.
    """
    if explicit:
        return explicit

    env_path = os.environ.get("ADSBTUI_CONFIG")
    if env_path:
        return env_path

    cwd_candidate = Path("adsb-tui.toml")
    if cwd_candidate.is_file():
        return str(cwd_candidate)

    xdg_home = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    xdg_candidate = Path(xdg_home) / "adsbtui" / "config.toml"
    if xdg_candidate.is_file():
        return str(xdg_candidate)

    return None


def load_toml(path: str | None) -> dict[str, Any]:
    """Load and parse a TOML config file into a plain dict.

    Returns {} if path is None. Any error reading or parsing an existing path (bad
    permissions, missing file that was explicitly named, malformed TOML) is wrapped in
    ConfigError.
    """
    if path is None:
        return {}

    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"config file {path!r} is not valid TOML: {e}") from e
    except OSError as e:
        raise ConfigError(f"could not read config file {path!r}: {e}") from e


# --------------------------------------------------------------------------------------
# Type coercion for env/CLI string values
# --------------------------------------------------------------------------------------

_TRUE_STRINGS = {"1", "true", "yes"}
_FALSE_STRINGS = {"0", "false", "no"}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    raise ConfigError(f"cannot parse {value!r} as a boolean (use 1/0/true/false/yes/no)")


def _parse_list_str(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _parse_list_int(value: Any) -> list[int]:
    if isinstance(value, list):
        return [int(v) for v in value]
    return [int(part.strip()) for part in str(value).split(",") if part.strip()]


def _coerce(default: Any, value: Any) -> Any:
    """Coerce a raw value (from TOML, an env string, or a CLI string) to the type implied
    by the dataclass field's default, based on that default's runtime type."""
    if isinstance(default, bool):
        return _parse_bool(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, list):
        if default and isinstance(default[0], int):
            return _parse_list_int(value)
        return _parse_list_str(value)
    return str(value)


# --------------------------------------------------------------------------------------
# Layering: TOML -> env -> CLI -> Config
# --------------------------------------------------------------------------------------


def _section_defaults(section_cls: type) -> dict[str, Any]:
    return {
        f.name: f.default_factory() if f.default_factory is not MISSING else f.default
        for f in fields(section_cls)
    }


def build_config(
    toml_dict: dict[str, Any],
    env: dict[str, str] | None = None,
    cli: dict[str, Any] | None = None,
    allow_null_home: bool = False,
) -> Config:
    """Build a validated Config by layering TOML, then env, then CLI (CLI wins).

    `toml_dict` is the dict returned by load_toml() -- nested {"section": {"key": val}}.
    `env` and `cli` are flat dicts keyed by dotted "section.key" strings, e.g.
    {"source.url": "http://...", "home.lat": "37.5"}. Values from env/cli are typically
    strings (env vars always are; CLI values may already be typed by argparse) and are
    coerced to the right type per-field; TOML values come already typed by tomllib but
    are still passed through the same coercion for uniformity.

    Raises ConfigError if any layer contains an unknown section or key, or if the
    resulting Config fails validate().
    """
    env = env or {}
    cli = cli or {}

    section_values: dict[str, dict[str, Any]] = {name: {} for name in _SECTIONS}

    for section_name, section_body in toml_dict.items():
        if section_name not in _SECTIONS:
            raise ConfigError(f"unknown config section {section_name!r} in TOML file")
        if not isinstance(section_body, dict):
            raise ConfigError(f"config section {section_name!r} must be a table")
        for key, value in section_body.items():
            section_values[section_name][key] = value

    for layer in (env, cli):
        for dotted_key, value in layer.items():
            if value is None:
                continue
            try:
                section_name, key = dotted_key.split(".", 1)
            except ValueError as e:
                raise ConfigError(
                    f"malformed override key {dotted_key!r} (want section.key)"
                ) from e
            if section_name not in _SECTIONS:
                raise ConfigError(
                    f"unknown config section {section_name!r} in override {dotted_key!r}"
                )
            section_values[section_name][key] = value

    sections: dict[str, Any] = {}
    for section_name, section_cls in _SECTIONS.items():
        defaults = _section_defaults(section_cls)
        raw = section_values[section_name]
        kwargs: dict[str, Any] = {}
        for key, value in raw.items():
            if key not in defaults:
                raise ConfigError(f"unknown config key {section_name}.{key!r}")
            try:
                kwargs[key] = _coerce(defaults[key], value)
            except (TypeError, ValueError, ConfigError) as e:
                raise ConfigError(
                    f"{section_name}.{key} has an invalid value {value!r}: {e}"
                ) from e
        sections[section_name] = section_cls(**kwargs)

    cfg = Config(**sections)
    validate(cfg, allow_null_home=allow_null_home)
    return cfg


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

_VALID_UNITS = {"imperial", "metric", "aviation"}
_VALID_BORDERS = {"unicode", "ascii", "none"}
_VALID_COLOR = {"auto", "always", "never"}
_VALID_WEBHOOK_FORMAT = {"auto", "json", "ntfy", "discord"}


def validate(cfg: Config, allow_null_home: bool = False) -> None:
    """Validate a Config in place, raising ConfigError on the first problem found.

    See the module docstring / task spec for the exact rules; each check names the
    "section.key" it is validating and the value that failed.
    """
    if not cfg.source.url:
        raise ConfigError("source.url is required and must not be empty")

    is_placeholder_home = cfg.home.lat == 0.0 and cfg.home.lon == 0.0
    if is_placeholder_home and not allow_null_home:
        raise ConfigError(
            "home.lat/home.lon are still the unedited (0.0, 0.0) placeholder; set your real "
            "home coordinates (pass allow_null_home=True if this is intentional, e.g. in tests)"
        )
    if not is_placeholder_home:
        if not (-90.0 <= cfg.home.lat <= 90.0):
            raise ConfigError(f"home.lat must be between -90 and 90, got {cfg.home.lat!r}")
        if not (-180.0 <= cfg.home.lon <= 180.0):
            raise ConfigError(f"home.lon must be between -180 and 180, got {cfg.home.lon!r}")

    if cfg.source.refresh_s <= 0:
        raise ConfigError(f"source.refresh_s must be positive, got {cfg.source.refresh_s!r}")
    if cfg.source.timeout_s <= 0:
        raise ConfigError(f"source.timeout_s must be positive, got {cfg.source.timeout_s!r}")
    if cfg.filter.radius <= 0:
        raise ConfigError(f"filter.radius must be positive, got {cfg.filter.radius!r}")
    if cfg.filter.proximity <= 0:
        raise ConfigError(f"filter.proximity must be positive, got {cfg.filter.proximity!r}")

    if cfg.display.units not in _VALID_UNITS:
        raise ConfigError(
            f"display.units must be one of {sorted(_VALID_UNITS)}, got {cfg.display.units!r}"
        )
    if cfg.display.borders not in _VALID_BORDERS:
        raise ConfigError(
            f"display.borders must be one of {sorted(_VALID_BORDERS)}, got {cfg.display.borders!r}"
        )
    if cfg.display.color not in _VALID_COLOR:
        raise ConfigError(
            f"display.color must be one of {sorted(_VALID_COLOR)}, got {cfg.display.color!r}"
        )
    if cfg.alerts.webhook_format not in _VALID_WEBHOOK_FORMAT:
        raise ConfigError(
            "alerts.webhook_format must be one of "
            f"{sorted(_VALID_WEBHOOK_FORMAT)}, got {cfg.alerts.webhook_format!r}"
        )


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser.

    Every flag defaults to None so cli_overrides() can distinguish "not passed" from
    "passed with a falsy value". Flags beyond config plumbing (--once, --watch, --headless,
    --batch, --format, --check) are parsed here but interpreted by other modules.
    """
    parser = argparse.ArgumentParser(
        prog="adsbtui",
        description="A curses TUI for tracking nearby aircraft from a dump1090/readsb ADS-B feed.",
    )

    parser.add_argument("--config", default=None, help="path to a TOML config file")
    parser.add_argument("--url", default=None, help="aircraft.json source: URL or file path")
    parser.add_argument("--lat", type=float, default=None, help="home latitude")
    parser.add_argument("--lon", type=float, default=None, help="home longitude")
    parser.add_argument("--radius", type=float, default=None, help="filter radius, in miles")
    parser.add_argument(
        "--proximity", type=float, default=None, help="proximity alert radius, in miles"
    )
    parser.add_argument("--refresh", type=float, default=None, help="poll interval, in seconds")
    parser.add_argument("--registry", default=None, help="path to an FAA registry CSV")
    parser.add_argument("--log-file", default=None, help="path to the log file")
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="log level",
    )
    parser.add_argument(
        "--units",
        default=None,
        choices=["imperial", "metric", "aviation"],
        help="display unit system",
    )
    parser.add_argument(
        "--no-color", action="store_true", default=False, help="disable color output"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="shortcut for --log-level DEBUG plus verbose diagnostics",
    )

    # Parsed here for a stable, single CLI surface; interpreted by other modules.
    parser.add_argument("--once", action="store_true", default=False, help="fetch once and exit")
    parser.add_argument(
        "--watch", type=int, default=None, help="run N poll cycles then exit"
    )
    parser.add_argument(
        "--headless", action="store_true", default=False, help="run without the curses UI"
    )
    parser.add_argument(
        "--batch", action="store_true", default=False, help="alias for --headless --once"
    )
    parser.add_argument(
        "--format",
        default=None,
        choices=["table", "json", "csv"],
        help="output format for headless/batch modes",
    )
    parser.add_argument(
        "--check", action="store_true", default=False, help="validate config and exit"
    )

    return parser


def cli_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """Map a parsed argparse.Namespace to the flat "section.key" -> value dict that
    build_config() expects, including only flags the user actually passed."""
    overrides: dict[str, Any] = {}

    def maybe(dotted_key: str, value: Any) -> None:
        if value is not None:
            overrides[dotted_key] = value

    maybe("source.url", args.url)
    maybe("home.lat", args.lat)
    maybe("home.lon", args.lon)
    maybe("filter.radius", args.radius)
    maybe("filter.proximity", args.proximity)
    maybe("source.refresh_s", args.refresh)
    maybe("registry.path", args.registry)
    maybe("logging.file", args.log_file)
    maybe("display.units", args.units)

    log_level = args.log_level
    if getattr(args, "debug", False):
        log_level = "DEBUG"
    maybe("logging.level", log_level)

    if getattr(args, "no_color", False):
        overrides["display.color"] = "never"

    return overrides


def _env_overrides(env: dict[str, str]) -> dict[str, str]:
    """Map an environment mapping (e.g. os.environ) to the flat "section.key" -> value
    dict build_config() expects, reading ADSBTUI_SECTION_KEY style names."""
    overrides: dict[str, str] = {}
    for section_name, section_cls in _SECTIONS.items():
        for f in fields(section_cls):
            env_name = f"ADSBTUI_{section_name.upper()}_{f.name.upper()}"
            if env_name in env:
                overrides[f"{section_name}.{f.name}"] = env[env_name]
    return overrides


# --------------------------------------------------------------------------------------
# Top-level entry point
# --------------------------------------------------------------------------------------


def load_config(argv: list[str] | None = None) -> Config:
    """Parse argv, read the environment, load the TOML config file, and build a fully
    validated Config.

    Returns the Config with two extra attributes attached for callers that need them:
    cfg._args (the parsed argparse.Namespace) and cfg._config_path (the resolved TOML
    path used, or None if no config file was found).
    """
    parser = build_argparser()
    args = parser.parse_args(argv)

    config_path = find_config_path(args.config)
    toml_dict = load_toml(config_path)

    env_dict = _env_overrides(dict(os.environ))
    cli_dict = cli_overrides(args)

    cfg = build_config(toml_dict, env=env_dict, cli=cli_dict)
    cfg._args = args
    cfg._config_path = config_path
    return cfg
