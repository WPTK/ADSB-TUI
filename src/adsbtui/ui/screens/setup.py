"""The first-run setup wizard: find a receiver, fix a home location, pick units, confirm.

A new user's first problem is not "which of 50 settings do I want", it is "what do I even
put in source.url" -- so this is a short sequence of steps rather than the full settings
form, and the receiver step does the guessing for them: given a hostname it probes the
handful of places dump1090-fa / readsb / tar1090 images actually publish aircraft.json,
reports which ones answered and how many aircraft each returned, and lets the user pick
one from that evidence.

SetupScreen follows the same Screen contract as every other screen (a title, a pure
render_lines(width, height), and a handle_key(key) returning "close" or None); curses is
imported only for KEY_* constants. Everything that touches the network or the filesystem
is injected: the constructor takes a 'prober' and a 'location_fetcher', defaulting to the
real implementations in this module, so the whole wizard is unit-testable with no terminal
and no receiver anywhere nearby.

Keys: Enter/F10 confirms the current step and moves on, Shift-Tab steps back, Tab moves
focus within a step, F5 (or Ctrl-P) runs the step's lookup action -- probing candidates on
the receiver step, reading receiver.json on the home step -- and Esc abandons the wizard.
On the final summary step Enter sets .applied and returns "close"; the caller then reads
result() and writes it out with config_write.dump_config().
"""

from __future__ import annotations

import curses
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from adsbtui.config import Config
from adsbtui.config_write import dump_config_str
from adsbtui.sources import SourceError, make_source
from adsbtui.ui.widgets import Form, ListPicker, NumberField, Select, TextField

_CANCEL_KEYS = frozenset({27})
_ADVANCE_KEYS = frozenset({10, 13, curses.KEY_ENTER, curses.KEY_F10})
_BACK_KEYS = frozenset({curses.KEY_BTAB})
_FOCUS_KEYS = frozenset({ord("\t")})
#: F5 or Ctrl-P (byte 16) runs the current step's lookup. Both are non-printable, so a
#: focused TextField cannot swallow them the way it would a plain letter.
_ACTION_KEYS = frozenset({curses.KEY_F5, 16})

STEP_RECEIVER = 0
STEP_HOME = 1
STEP_UNITS = 2
STEP_SUMMARY = 3
_STEP_NAMES = ("Receiver", "Home location", "Units and radius", "Summary")

#: The aircraft.json locations worth guessing, in the order they are tried. The HTTP ones
#: cover tar1090, FlightAware's SkyAware alias, dump1090-fa's own alias, and readsb's
#: built-in server on :8080; the last two are the local files a receiver publishes when
#: adsbtui runs on the receiver itself.
_HTTP_TEMPLATES = (
    "http://{host}/tar1090/data/aircraft.json",
    "http://{host}/skyaware/data/aircraft.json",
    "http://{host}/dump1090-fa/data/aircraft.json",
    "http://{host}:8080/data/aircraft.json",
)
_LOCAL_PATHS = (
    "/run/readsb/aircraft.json",
    "/run/dump1090-fa/aircraft.json",
)

#: Probes are meant to feel like a lookup, not a poll, so they use a much shorter timeout
#: than the running fetch loop: six candidates at 5s each would be a 30-second freeze.
PROBE_TIMEOUT_S = 2.0

_UNIT_CHOICES: list[tuple[str, str]] = [
    ("imperial", "Imperial (mi / ft / mph)"),
    ("metric", "Metric (km / m / km-h)"),
    ("aviation", "Aviation (nm / ft / kt)"),
]

#: Cap on how much of a receiver.json is read before giving up; it is a tiny file, and a
#: URL that turns out to serve something enormous should not be swallowed whole.
_RECEIVER_JSON_MAX_BYTES = 64 * 1024


def _clip(text: str, width: int) -> str:
    """Truncate text to at most width characters; empty string for width <= 0."""
    if width <= 0:
        return ""
    return text[:width] if len(text) > width else text


@dataclass(frozen=True)
class ProbeResult:
    """One probed aircraft.json candidate and what came back from it."""

    candidate: str
    ok: bool
    aircraft_count: int | None = None
    error: str = ""


def candidate_sources(host_or_url: str) -> list[str]:
    """The aircraft.json candidates to probe for a typed host, URL, or path.

    A bare hostname or IP expands to every HTTP layout in _HTTP_TEMPLATES; anything that
    already looks like a URL or an absolute path is taken literally and probed as given.
    Either way the two local file paths are appended, since they do not depend on what was
    typed and cost nothing to check when adsbtui is running on the receiver itself.
    """
    text = host_or_url.strip()
    candidates: list[str] = []
    if text.startswith(("http://", "https://", "file://", "/")):
        candidates.append(text)
    elif text:
        candidates.extend(template.format(host=text) for template in _HTTP_TEMPLATES)
    candidates.extend(_LOCAL_PATHS)

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def probe_sources(host_or_url: str, timeout: float = PROBE_TIMEOUT_S) -> list[ProbeResult]:
    """Try every candidate for host_or_url and report what each one did.

    Every candidate is tried even after one succeeds: the user picks from the full picture
    (a receiver often answers on two paths, and "3 aircraft" versus "41 aircraft" is the
    only way to tell a stale alias from the live feed). Each candidate's SourceError is
    recorded against that candidate rather than aborting the sweep.
    """
    results: list[ProbeResult] = []
    for candidate in candidate_sources(host_or_url):
        try:
            payload = make_source(candidate).fetch(timeout)
        except SourceError as exc:
            results.append(ProbeResult(candidate, False, None, str(exc)))
            continue
        aircraft = payload.get("aircraft")
        count = len(aircraft) if isinstance(aircraft, list) else 0
        results.append(ProbeResult(candidate, True, count))
    return results


def receiver_json_for(aircraft_url: str) -> str:
    """The receiver.json sitting next to a given aircraft.json URL or path."""
    head, sep, _tail = aircraft_url.rpartition("/")
    if not sep:
        return "receiver.json"
    return f"{head}/receiver.json"


def _load_json(url_or_path: str, timeout: float) -> dict | None:
    """Read one small JSON object over HTTP or off disk; None on any failure.

    make_source() is deliberately not reused here: its Sources reject anything without an
    "aircraft" list, which is exactly what receiver.json is.
    """
    try:
        if url_or_path.startswith(("http://", "https://")):
            request = urllib.request.Request(url_or_path, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(_RECEIVER_JSON_MAX_BYTES)
        else:
            path = url_or_path
            if path.startswith("file://"):
                path = path[len("file://") :]
            with open(path, "rb") as handle:
                body = handle.read(_RECEIVER_JSON_MAX_BYTES)
        data = json.loads(body)
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fetch_receiver_location(
    aircraft_url: str, timeout: float = PROBE_TIMEOUT_S
) -> tuple[float, float] | None:
    """Read lat/lon out of the receiver.json next to aircraft_url, or None.

    readsb and dump1090-fa both publish their configured site location there, so a user
    who already told their receiver where it is does not have to look it up again. None
    covers every "it isn't there" case -- unreachable, unparseable, or simply a
    receiver.json with no location in it (which is what an unconfigured receiver serves).
    """
    data = _load_json(receiver_json_for(aircraft_url), timeout)
    if data is None:
        return None
    lat = data.get("lat")
    lon = data.get("lon")
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    return float(lat), float(lon)


def _probe_label(result: ProbeResult) -> str:
    if result.ok:
        return f"{result.candidate}  --  responded, {result.aircraft_count or 0} aircraft"
    return f"{result.candidate}  --  no data ({result.error})"


class SetupScreen:
    """The four-step first-run wizard, producing a ready-to-write Config.

    prober(host_or_url) -> sequence of ProbeResult and location_fetcher(aircraft_url) ->
    (lat, lon) or None are injection points for the two side-effecting lookups; both
    default to this module's real implementations. initial_host pre-fills the receiver
    field, e.g. from a --url the user already passed.
    """

    def __init__(
        self,
        prober: Callable[[str], Sequence[ProbeResult]] | None = None,
        location_fetcher: Callable[[str], tuple[float, float] | None] | None = None,
        initial_host: str = "",
    ) -> None:
        self._prober = prober if prober is not None else probe_sources
        self._location_fetcher = (
            location_fetcher if location_fetcher is not None else fetch_receiver_location
        )

        defaults = Config()
        self._step = STEP_RECEIVER
        self.applied = False
        self.error: str | None = None
        self.message: str | None = None

        self._host_field = TextField(initial=initial_host)
        self._probe_results: list[ProbeResult] = []
        self._picker: ListPicker | None = None
        self._picker_focused = False

        self._lat_field = NumberField()
        self._lon_field = NumberField()
        self._home_form = Form([("Latitude", self._lat_field), ("Longitude", self._lon_field)])

        self._units_select = Select(_UNIT_CHOICES)
        self._radius_field = NumberField(initial=repr(defaults.filter.radius))
        self._units_form = Form(
            [("Units", self._units_select), ("Radius (mi)", self._radius_field)]
        )

        # Values committed by each completed step; result() is assembled from these, never
        # from half-typed field text.
        self._source_url = ""
        self._lat = 0.0
        self._lon = 0.0
        self._units = defaults.display.units
        self._radius = defaults.filter.radius

    # ----------------------------------------------------------------------------------
    # Screen contract
    # ----------------------------------------------------------------------------------

    @property
    def title(self) -> str:
        return f"Setup {self._step + 1}/{len(_STEP_NAMES)}: {_STEP_NAMES[self._step]}"

    @property
    def step(self) -> int:
        return self._step

    @property
    def probe_results(self) -> list[ProbeResult]:
        """The last probe's results, in the order they were tried (empty before a probe)."""
        return list(self._probe_results)

    def handle_key(self, key: int) -> str | None:
        if key in _CANCEL_KEYS:
            return "close"
        if key in _BACK_KEYS:
            self._go_back()
            return None
        if key in _ACTION_KEYS:
            self._run_action()
            return None
        if key in _ADVANCE_KEYS:
            return self._advance()
        if key in _FOCUS_KEYS and self._step == STEP_RECEIVER:
            if self._picker is not None:
                self._picker_focused = not self._picker_focused
            return None
        widget = self._focused_widget()
        if widget is not None:
            widget.handle_key(key)
        return None

    def result(self) -> Config:
        """The Config the wizard has assembled so far, as a brand-new object.

        Everything the wizard does not ask about keeps its schema default, which is the
        point: a first run should produce a small, comprehensible config file, not a dump
        of every key the program knows.
        """
        cfg = Config()
        cfg.source.url = self._source_url
        cfg.home.lat = self._lat
        cfg.home.lon = self._lon
        cfg.display.units = self._units
        cfg.filter.radius = self._radius
        return cfg

    # ----------------------------------------------------------------------------------
    # Step navigation
    # ----------------------------------------------------------------------------------

    def _focused_widget(self) -> Any | None:
        """The widget the current step's keys are delegated to (a Form counts as one).

        None on the summary step, which has nothing to edit -- a stray keypress there must
        not quietly change an answer the user can no longer see.
        """
        if self._step == STEP_RECEIVER:
            if self._picker_focused and self._picker is not None:
                return self._picker
            return self._host_field
        if self._step == STEP_HOME:
            return self._home_form
        if self._step == STEP_UNITS:
            return self._units_form
        return None

    def _go_back(self) -> None:
        if self._step > STEP_RECEIVER:
            self._step -= 1
            self.error = None
            self.message = None

    def _advance(self) -> str | None:
        """Validate the current step and move on, or stay put with .error set."""
        self.error = None
        self.message = None
        if self._step == STEP_RECEIVER:
            if not self._commit_receiver():
                return None
        elif self._step == STEP_HOME:
            if not self._commit_home():
                return None
        elif self._step == STEP_UNITS:
            if not self._commit_units():
                return None
        else:
            self.applied = True
            return "close"
        self._step += 1
        return None

    def _run_action(self) -> None:
        self.error = None
        self.message = None
        if self._step == STEP_RECEIVER:
            self._probe()
        elif self._step == STEP_HOME:
            self._fill_home_from_receiver()

    # ----------------------------------------------------------------------------------
    # Step 1: receiver
    # ----------------------------------------------------------------------------------

    def _probe(self) -> None:
        self._probe_results = list(self._prober(self._host_field.value.strip()))
        if not self._probe_results:
            self._picker = None
            self._picker_focused = False
            self.error = "nothing to probe: type a hostname, URL, or path first"
            return

        self._picker = ListPicker(
            self._probe_results,
            visible_rows=max(1, len(self._probe_results)),
            labeler=_probe_label,
        )
        # Park the cursor on the first candidate that actually answered, so the common
        # case is "probe, then press Enter".
        first_ok = next((i for i, result in enumerate(self._probe_results) if result.ok), None)
        for _ in range(first_ok or 0):
            self._picker.handle_key(curses.KEY_DOWN)
        self._picker_focused = True

        hits = sum(1 for result in self._probe_results if result.ok)
        if hits:
            self.message = f"{hits} of {len(self._probe_results)} candidates responded"
        else:
            self.error = "no candidate responded; check the hostname and try again"

    def _commit_receiver(self) -> bool:
        if self._picker is not None and self._picker_focused:
            selected = self._probe_results[self._picker.cursor]
            if not selected.ok:
                self.error = f"{selected.candidate} did not return aircraft data"
                return False
            self._source_url = selected.candidate
            return True

        typed = self._host_field.value.strip()
        if not typed:
            self.error = "enter a hostname, URL, or path (F5 probes the usual locations)"
            return False
        self._source_url = typed
        return True

    # ----------------------------------------------------------------------------------
    # Step 2: home location
    # ----------------------------------------------------------------------------------

    def _fill_home_from_receiver(self) -> None:
        if not self._source_url:
            self.error = "pick a receiver first"
            return
        location = self._location_fetcher(self._source_url)
        if location is None:
            self.error = f"no location in {receiver_json_for(self._source_url)}; enter it by hand"
            return
        lat, lon = location
        self._lat_field.value = repr(float(lat))
        self._lon_field.value = repr(float(lon))
        self.message = f"read {lat}, {lon} from receiver.json"

    def _commit_home(self) -> bool:
        lat = self._lat_field.value
        lon = self._lon_field.value
        if lat is None or lon is None:
            self.error = "enter both a latitude and a longitude"
            return False
        if lat == 0.0 and lon == 0.0:
            self.error = "(0.0, 0.0) is the unedited placeholder; enter your real location"
            return False
        if not -90.0 <= lat <= 90.0:
            self.error = f"latitude must be between -90 and 90, got {lat}"
            return False
        if not -180.0 <= lon <= 180.0:
            self.error = f"longitude must be between -180 and 180, got {lon}"
            return False
        self._lat = lat
        self._lon = lon
        return True

    # ----------------------------------------------------------------------------------
    # Step 3: units and radius
    # ----------------------------------------------------------------------------------

    def _commit_units(self) -> bool:
        radius = self._radius_field.value
        if radius is None or radius <= 0:
            self.error = "radius must be a positive number of miles"
            return False
        self._units = self._units_select.value
        self._radius = radius
        return True

    # ----------------------------------------------------------------------------------
    # Rendering (pure)
    # ----------------------------------------------------------------------------------

    def _receiver_lines(self, width: int) -> list[str]:
        focus = " " if self._picker_focused else ">"
        lines = [
            _clip("Where is your receiver? A hostname or IP is enough.", width),
            "",
            _clip(f"{focus} Host/URL: {self._host_field.value}", width),
            "",
            _clip("F5 probes the usual aircraft.json locations:", width),
        ]
        if self._picker is None:
            lines.extend(
                _clip(f"    {candidate}", width)
                for candidate in candidate_sources(self._host_field.value.strip())
            )
            return lines

        picker_focus = ">" if self._picker_focused else " "
        lines.append(_clip(f"{picker_focus} Results (Up/Down to choose, Enter to use):", width))
        lines.extend(_clip("  " + line, width) for line in self._picker.render_lines(width - 2))
        return lines

    def _home_lines(self, width: int) -> list[str]:
        lines = [
            _clip("Where are you? Distances and bearings are measured from here.", width),
            "",
        ]
        lines.extend(_clip(line, width) for line in self._home_form.render_lines(width))
        lines.extend(
            [
                "",
                _clip("F5 reads lat/lon from the receiver.json next to your feed:", width),
                _clip(f"    {receiver_json_for(self._source_url)}", width)
                if self._source_url
                else "",
            ]
        )
        return lines

    def _units_lines(self, width: int) -> list[str]:
        lines = [
            _clip("How should distances be shown, and how far out should we look?", width),
            "",
        ]
        lines.extend(_clip(line, width) for line in self._units_form.render_lines(width))
        return lines

    def _summary_lines(self, width: int) -> list[str]:
        cfg = self.result()
        # The four answers come first and always fit; the full file follows for anyone who
        # wants to read it before it is written, and is what gets clipped on a short
        # terminal rather than the answers themselves.
        lines = [
            _clip("Ready to write this configuration:", width),
            "",
            _clip(f"    Receiver: {cfg.source.url}", width),
            _clip(f"    Home:     {cfg.home.lat}, {cfg.home.lon}", width),
            _clip(f"    Units:    {cfg.display.units}", width),
            _clip(f"    Radius:   {cfg.filter.radius} mi", width),
            "",
            _clip("Every other setting keeps its default. The full file:", width),
            "",
        ]
        lines.extend(_clip(line, width) for line in dump_config_str(cfg).splitlines())
        return lines

    def render_lines(self, width: int, height: int) -> list[str]:
        if width <= 0 or height <= 0:
            return []

        head = [_clip(self.title, width), ""]
        tail = [""]
        if self.error:
            tail.append(_clip(f"! {self.error}", width))
        elif self.message:
            tail.append(_clip(f"- {self.message}", width))
        tail.append(_clip(self._footer(), width))

        capacity = height - len(head) - len(tail)
        if capacity < 1:
            return (head + tail)[:height]

        if self._step == STEP_RECEIVER:
            body = self._receiver_lines(width)
        elif self._step == STEP_HOME:
            body = self._home_lines(width)
        elif self._step == STEP_UNITS:
            body = self._units_lines(width)
        else:
            body = self._summary_lines(width)

        return head + body[:capacity] + tail

    def _footer(self) -> str:
        if self._step == STEP_RECEIVER:
            return "F5 probe   Tab focus   Enter next   Esc cancel"
        if self._step == STEP_HOME:
            return "F5 read receiver.json   Tab/Up/Down field   Enter next   Shift-Tab back"
        if self._step == STEP_UNITS:
            return "Tab/Up/Down field   Left/Right units   Enter next   Shift-Tab back"
        return "Enter finish   Shift-Tab back   Esc cancel"
