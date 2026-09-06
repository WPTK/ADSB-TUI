"""The Settings screen: edit every config key from inside the running TUI.

SettingsScreen follows the Screen design contract documented in ui/screens/__init__.py: a
read-only title, a pure render_lines(width, height) with no curses calls, and a
handle_key(key) that returns a short navigation signal or None. curses is imported only
for its KEY_* integer constants, which is safe without an initialized screen.

The field list is *derived* from config.Config itself rather than hand-listed, so a new
key added to a config section automatically appears here instead of silently becoming
uneditable. Each field gets a widget chosen by the type of its dataclass default (str ->
TextField, bool -> Toggle, int/float -> NumberField, list -> a TextField holding a
comma-separated list), except for the handful of keys whose value must be one of a fixed
set -- display.units, display.color, display.borders, display.density and
alerts.webhook_format -- which get a Select. Every field also carries a one-line help
string; those strings are, in practice, the closest thing this project has to in-app
documentation of each setting, so they are written to be read by a user rather than by a
maintainer.

Two user intents are deliberately distinct, because they are distinct in real use --
trying a setting for this session is not the same as committing it to disk:

  * Enter or F10 *applies*: handle_key returns "close" with .applied True. The caller
    adopts result() as the running config and writes nothing.
  * F2 or Ctrl-S *applies and saves*: handle_key returns "close-and-save" with both
    .applied and .save_requested True. The caller adopts result() and persists it with
    config_write.dump_config().
  * Esc *cancels*: handle_key returns "close" with .applied False, and result() still
    reports the values the screen was opened with.

Applying never raises. The candidate Config is run through config.validate() first, and a
ConfigError is caught and shown as an inline error line with the screen left open, so a
typo like a latitude of 200 is something the user fixes in place rather than something
that blows up the TUI.
"""

from __future__ import annotations

import curses
from collections.abc import Sequence
from dataclasses import dataclass, fields, is_dataclass
from typing import Any

from adsbtui.config import Config, ConfigError, validate
from adsbtui.ui.widgets import Form, NumberField, Select, Tabs, TextField, Toggle

_CANCEL_KEYS = frozenset({27})
_APPLY_KEYS = frozenset({10, 13, curses.KEY_ENTER, curses.KEY_F10})
#: F2, or Ctrl-S (byte 19). Both are non-printable, so neither can be swallowed by a
#: focused TextField the way a plain letter would be.
_SAVE_KEYS = frozenset({curses.KEY_F2, 19})
_NEXT_TAB_KEYS = frozenset({ord("\t")})
_PREV_TAB_KEYS = frozenset({curses.KEY_BTAB})

_FOOTER_TEXT = "Tab/Shift-Tab section   Up/Down field   Enter apply   F2 apply+save   Esc cancel"

#: dotted "section.key" -> the (value, label) choices offered for it by a Select.
_CHOICES: dict[str, list[tuple[str, str]]] = {
    "display.units": [
        ("imperial", "Imperial"),
        ("metric", "Metric"),
        ("aviation", "Aviation"),
    ],
    "display.color": [("auto", "Auto"), ("always", "Always"), ("never", "Never")],
    "display.borders": [("unicode", "Unicode"), ("ascii", "ASCII"), ("none", "None")],
    "display.density": [("compact", "Compact"), ("normal", "Normal"), ("wide", "Wide")],
    "alerts.webhook_format": [
        ("auto", "Auto"),
        ("json", "JSON"),
        ("ntfy", "ntfy"),
        ("discord", "Discord"),
    ],
}

#: dotted "section.key" -> (field label, one-line help). This is the user-facing
#: documentation of the config schema; keep it in sync with the README's key tables.
_LABELS_AND_HELP: dict[str, tuple[str, str]] = {
    "source.url": (
        "aircraft.json",
        "http(s) URL, bare path, or file:// URL of your receiver's aircraft.json.",
    ),
    "source.refresh_s": ("Refresh (s)", "Seconds between polls of the source."),
    "source.timeout_s": ("Timeout (s)", "Give up on a single fetch after this many seconds."),
    "source.stale_s": (
        "Feed stale (s)",
        "Age at which the feed's own data is treated as stale.",
    ),
    "source.backoff_max_s": (
        "Backoff max (s)",
        "Ceiling for the retry backoff (1s, 2s, 4s, ...) after a failed fetch.",
    ),
    "source.max_bytes": (
        "Max response",
        "Reject a response larger than this many bytes, guarding against a runaway feed.",
    ),
    "home.lat": (
        "Latitude",
        "Home latitude in degrees, -90 to 90. All distances are measured from here.",
    ),
    "home.lon": ("Longitude", "Home longitude in degrees, -180 to 180."),
    "filter.radius": ("Radius (mi)", "Drop aircraft farther than this many miles from home."),
    "filter.proximity": (
        "Proximity (mi)",
        "Distance at which an aircraft is graded OVERHEAD.",
    ),
    "filter.hide_ground": (
        "Hide ground",
        "Hide aircraft that report they are on the ground.",
    ),
    "filter.include_nonicao": (
        "Include non-ICAO",
        "Include ~-prefixed TIS-B/MLAT addresses that have no real ICAO hex.",
    ),
    "filter.min_alt_ft": ("Min altitude (ft)", "Hide aircraft below this altitude."),
    "filter.max_alt_ft": ("Max altitude (ft)", "Hide aircraft above this altitude."),
    "display.units": (
        "Units",
        "imperial (mi/ft/mph), metric (km/m/km-h), or aviation (nm/ft/kt).",
    ),
    "display.theme": ("Theme", "Name of the color theme; only 'default' exists today."),
    "display.color": (
        "Color",
        "auto follows the terminal, always forces color on, never disables it.",
    ),
    "display.columns": ("Columns", "Comma-separated column keys, in display order."),
    "display.owner_width": (
        "Owner width",
        "Preferred width, in characters, of the owner column.",
    ),
    "display.borders": (
        "Borders",
        "Box-drawing style for the header divider and column separators.",
    ),
    "display.density": ("Density", "Row density: compact, normal, or wide."),
    "display.sort_key": (
        "Sort key",
        "What the table is sorted by: distance, altitude, or callsign.",
    ),
    "display.sort_reverse": ("Reverse sort", "Sort descending instead of ascending."),
    "display.stale_after_s": (
        "Row stale (s)",
        "Seconds without a position update before a row is dimmed as stale.",
    ),
    "display.linger_s": (
        "Linger (s)",
        "Seconds a vanished aircraft stays listed, marked lost, before removal.",
    ),
    "display.status_bar": ("Status bar", "Show the bottom status line."),
    "display.vs_threshold_fpm": (
        "Climb threshold",
        "Vertical speed, ft/min, above which a row reads as climbing or descending.",
    ),
    "alerts.emergency": (
        "Emergency",
        "Grade a watched squawk or a reported emergency state as EMERGENCY.",
    ),
    "alerts.squawks": (
        "Squawks",
        "Comma-separated squawk codes graded as emergencies.",
    ),
    "alerts.emergency_ignore_radius": (
        "Emergency any range",
        "Show an emergency aircraft even when it is outside the filter radius.",
    ),
    "alerts.cpa_enabled": (
        "CPA alerts",
        "Project the closest point of approach and grade an approaching aircraft INBOUND.",
    ),
    "alerts.cpa_distance": (
        "CPA distance (mi)",
        "How close a projected pass must be to count as INBOUND.",
    ),
    "alerts.cpa_horizon_s": (
        "CPA horizon (s)",
        "How far into the future a CPA solution may be and still count.",
    ),
    "alerts.cpa_min_gs_kt": (
        "CPA min speed (kt)",
        "Ignore CPA projections for aircraft slower than this.",
    ),
    "alerts.events": (
        "Events",
        "Comma-separated event families to dispatch: proximity, cpa, watchlist, emergency.",
    ),
    "alerts.bell": ("Bell", "Ring the terminal bell on a dispatched alert."),
    "alerts.desktop": (
        "Desktop notify",
        "Send a desktop notification via notify-send or osascript.",
    ),
    "alerts.webhook_url": (
        "Webhook URL",
        "POST a payload here on a dispatched alert; empty disables webhooks.",
    ),
    "alerts.webhook_format": (
        "Webhook format",
        "Payload shape: auto picks from the URL, or force json, ntfy, or discord.",
    ),
    "alerts.command": (
        "Command",
        "Command run on an alert, with HEX/REG/CALL/DIST/ALT in its environment.",
    ),
    "alerts.cooldown_s": (
        "Cooldown (s)",
        "Minimum seconds between repeat alerts for the same aircraft and event.",
    ),
    "alerts.quiet_hours": (
        "Quiet hours",
        "HH:MM-HH:MM window (may wrap past midnight) that silences bell and desktop.",
    ),
    "watchlist.path": (
        "Watchlist file",
        "Plain-text file of hex codes, registrations, callsigns, and types to watch.",
    ),
    "watchlist.pin_top": ("Pin to top", "Sort watchlist matches above everything else."),
    "watchlist.ignore_radius": (
        "Ignore radius",
        "Show a watchlist match even when it is outside the filter radius.",
    ),
    "registry.db": (
        "Registry database",
        "SQLite cache of aircraft registrations built from tar1090-db or the FAA registry.",
    ),
    "registry.max_age_days": (
        "Warn when older than",
        "Days before the Data screen flags a downloaded database as stale.",
    ),
    "registry.path": (
        "Registry CSV",
        "FAA registry CSV used to fill the owner column; empty disables owner lookup.",
    ),
    "history.db": (
        "History database",
        "SQLite file recording closed tracks; empty disables history.",
    ),
    "history.retention_days": (
        "Retention (days)",
        "Delete history rows older than this many days.",
    ),
    "logging.file": ("Log file", "Rotating log file path."),
    "logging.level": ("Log level", "DEBUG, INFO, WARNING, or ERROR."),
    "logging.max_bytes": ("Rotate at", "Rotate the log once it reaches this many bytes."),
    "logging.backup_count": ("Backups kept", "How many rotated log files to keep."),
    "logging.redact_home": ("Redact home", "Keep home coordinates out of log output."),
}


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


def _kind_of(dotted: str, default: Any) -> str:
    """Classify a config field by the runtime type of its dataclass default.

    Mirrors config._coerce()'s dispatch order -- bool before int, because bool is an int
    subclass -- so the widget a field gets and the type the loader coerces it to can never
    disagree.
    """
    if dotted in _CHOICES:
        return "choice"
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, float):
        return "float"
    if isinstance(default, int):
        return "int"
    if isinstance(default, list):
        return "list_int" if default and isinstance(default[0], int) else "list_str"
    return "str"


@dataclass(frozen=True)
class _FieldSpec:
    """One editable config key: where it lives, how it is labelled, and how it is typed."""

    section: str
    key: str
    label: str
    help: str
    kind: str

    @property
    def dotted(self) -> str:
        return f"{self.section}.{self.key}"


def _build_specs() -> list[tuple[str, list[_FieldSpec]]]:
    """Walk config.Config and return [(section name, [field specs])] in declaration order.

    Private bookkeeping attributes (_args, _config_path) and anything that is not a
    dataclass section are skipped. A key with no entry in _LABELS_AND_HELP still gets a
    field -- derived from its name, with no help text -- so a newly added config key is
    editable from the moment it exists, and only its documentation lags.
    """
    defaults = Config()
    sections: list[tuple[str, list[_FieldSpec]]] = []
    for section_field in fields(defaults):
        if section_field.name.startswith("_"):
            continue
        section_obj = getattr(defaults, section_field.name)
        if not is_dataclass(section_obj):
            continue
        specs: list[_FieldSpec] = []
        for value_field in fields(section_obj):
            dotted = f"{section_field.name}.{value_field.name}"
            label, help_text = _LABELS_AND_HELP.get(
                dotted, (value_field.name.replace("_", " "), "")
            )
            specs.append(
                _FieldSpec(
                    section=section_field.name,
                    key=value_field.name,
                    label=label,
                    help=help_text,
                    kind=_kind_of(dotted, getattr(section_obj, value_field.name)),
                )
            )
        sections.append((section_field.name, specs))
    return sections


def _float_text(value: float) -> str:
    return repr(float(value))


def _index_of(choices: Sequence[tuple[Any, str]], value: Any) -> int:
    for i, (choice_value, _label) in enumerate(choices):
        if choice_value == value:
            return i
    return 0


class SettingsScreen:
    """Edit an entire Config across one tab per section, then apply, apply-and-save, or
    cancel.

    The Config passed in is never mutated: widgets are seeded from copies of its values,
    and result() returns a freshly constructed Config. The two bookkeeping attributes
    load_config() stamps on a Config (_args, _config_path) are carried across to the
    result, so a caller that wants to save knows which file the config came from.
    """

    def __init__(self, cfg: Config) -> None:
        self._source_args = cfg._args
        self._source_path = cfg._config_path

        self._sections = _build_specs()
        self._widgets: dict[str, Any] = {}
        self._initial_numbers: dict[str, float] = {}
        self._forms: list[Form] = []

        for section_name, specs in self._sections:
            section_obj = getattr(cfg, section_name)
            form_fields: list[tuple[str, Any]] = []
            for spec in specs:
                widget = self._make_widget(spec, getattr(section_obj, spec.key))
                self._widgets[spec.dotted] = widget
                form_fields.append((spec.label, widget))
            self._forms.append(Form(form_fields))

        self._tabs = Tabs([name.capitalize() for name, _specs in self._sections])

        self.applied = False
        self.save_requested = False
        self.error: str | None = None
        # Seeding the committed result straight from the freshly built widgets means
        # "cancel" and "apply without editing anything" go through exactly one code path,
        # and proves at construction time that every field round-trips through its widget.
        self._committed: Config = self._config_from_widgets()

    # ----------------------------------------------------------------------------------
    # Widget construction and read-back
    # ----------------------------------------------------------------------------------

    def _make_widget(self, spec: _FieldSpec, value: Any) -> Any:
        if spec.kind == "choice":
            choices = _CHOICES[spec.dotted]
            return Select(choices, initial_index=_index_of(choices, value))
        if spec.kind == "bool":
            return Toggle(initial=bool(value))
        if spec.kind == "int":
            self._initial_numbers[spec.dotted] = float(value)
            return NumberField(initial=str(int(value)))
        if spec.kind == "float":
            self._initial_numbers[spec.dotted] = float(value)
            return NumberField(initial=_float_text(value))
        if spec.kind == "list_str":
            return TextField(initial=", ".join(str(item) for item in value))
        if spec.kind == "list_int":
            return TextField(initial=", ".join(str(int(item)) for item in value))
        return TextField(initial=str(value))

    def _read_widget(self, spec: _FieldSpec) -> Any:
        """Current value of one field, in the type its config section expects.

        A NumberField left empty or mid-edit (a bare "-") parses as None; that falls back
        to the value the field was opened with, so read-back always yields a usable number
        rather than a hole in the Config.
        """
        widget = self._widgets[spec.dotted]
        if spec.kind in ("choice", "bool"):
            return widget.value
        if spec.kind == "int":
            number = widget.value
            if number is None:
                number = self._initial_numbers[spec.dotted]
            return int(round(number))
        if spec.kind == "float":
            number = widget.value
            if number is None:
                number = self._initial_numbers[spec.dotted]
            return float(number)
        if spec.kind == "list_str":
            return [part.strip() for part in widget.value.split(",") if part.strip()]
        if spec.kind == "list_int":
            parts = [part.strip() for part in widget.value.split(",") if part.strip()]
            try:
                return [int(part) for part in parts]
            except ValueError as exc:
                raise ConfigError(f"{spec.dotted} must be a list of whole numbers: {exc}") from exc
        return widget.value

    def _config_from_widgets(self) -> Config:
        """Build a brand-new Config from the widgets' current values.

        Raises ConfigError if a field's text cannot be parsed into its declared type; the
        result is *not* validated here (validate() is a separate pass, so the caller can
        report a parse problem and a validation problem the same way).
        """
        defaults = Config()
        kwargs: dict[str, Any] = {}
        for section_name, specs in self._sections:
            section_cls = type(getattr(defaults, section_name))
            kwargs[section_name] = section_cls(
                **{spec.key: self._read_widget(spec) for spec in specs}
            )
        cfg = Config(**kwargs)
        cfg._args = self._source_args
        cfg._config_path = self._source_path
        return cfg

    # ----------------------------------------------------------------------------------
    # Screen contract
    # ----------------------------------------------------------------------------------

    @property
    def title(self) -> str:
        return "Settings"

    @property
    def active_tab(self) -> int:
        return self._tabs.active_index

    @property
    def active_section(self) -> str:
        return self._sections[self._tabs.active_index][0]

    @property
    def focus_index(self) -> int:
        """Index of the focused field within the active section's form."""
        return self._forms[self._tabs.active_index].focus_index

    def _commit(self) -> bool:
        """Try to adopt the widgets' current state as the committed result.

        Returns True on success. On failure the message is stashed in .error and the
        previously committed Config is left alone, which is what keeps the screen open and
        the running config untouched after a bad edit.
        """
        self.error = None
        try:
            candidate = self._config_from_widgets()
            validate(candidate)
        except ConfigError as exc:
            self.error = str(exc)
            return False
        self._committed = candidate
        return True

    def handle_key(self, key: int) -> str | None:
        if key in _CANCEL_KEYS:
            return "close"
        if key in _NEXT_TAB_KEYS:
            self._tabs.handle_key(ord("\t"))
            return None
        if key in _PREV_TAB_KEYS:
            self._tabs.handle_key(curses.KEY_BTAB)
            return None
        if key in _APPLY_KEYS:
            if not self._commit():
                return None
            self.applied = True
            return "close"
        if key in _SAVE_KEYS:
            if not self._commit():
                return None
            self.applied = True
            self.save_requested = True
            return "close-and-save"
        self._forms[self._tabs.active_index].handle_key(key)
        return None

    def result(self) -> Config:
        """The committed Config: a new object equal to the one the screen was opened with
        unless an apply (or apply-and-save) succeeded first.

        Always safe to call; callers that only want deliberate changes check .applied.
        """
        return self._committed

    # ----------------------------------------------------------------------------------
    # Rendering (pure)
    # ----------------------------------------------------------------------------------

    def _body_lines(self, width: int) -> tuple[list[str], int]:
        """Every field line for the active section, each followed by its help line.

        Also returns the body index of the focused field's own line, which is what the
        scroll window in render_lines() keeps on screen.
        """
        index = self._tabs.active_index
        form = self._forms[index]
        specs = self._sections[index][1]
        field_lines = form.render_lines(width)

        body: list[str] = []
        focus_line = 0
        for i, spec in enumerate(specs):
            if i == form.focus_index:
                focus_line = len(body)
            body.append(field_lines[i] if i < len(field_lines) else "")
            body.append(_clip("      " + spec.help, width) if spec.help else "")
        return body, focus_line

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        head = [_clip(self._tabs.render_lines(width)[0], width), ""]
        tail = [""]
        if self.error:
            tail.append(_clip(f"! {self.error}", width))
        tail.append(_clip(_FOOTER_TEXT, width))

        capacity = height - len(head) - len(tail)
        if capacity < 1:
            return (head + tail)[:height]

        body, focus_line = self._body_lines(width)
        if len(body) > capacity:
            # Keep the focused field and its help line both visible when they fit.
            start = max(0, min(focus_line + 2 - capacity, len(body) - capacity))
            body = body[start : start + capacity]

        return head + body + tail
