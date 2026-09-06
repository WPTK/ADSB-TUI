"""Alert dispatch: turns a graded AlertLevel change into actual notifications.

alerts.grade() only decides *how alarmed* the UI should be about an aircraft; it never
rings a bell, pops a desktop notification, POSTs a webhook, or runs a shell command. This
module is the layer that sits on top of grade()'s output and actually does those things,
subject to the [alerts] config knobs: which events are enabled, per-(hex, event) cooldowns,
quiet hours, and which of bell/desktop/webhook/command channels are turned on.

Like alerts.py, the *decision* logic here is kept separable from the side effects so it can
be unit-tested without a terminal, a network, or a subprocess actually running:

  * build_event() is a pure function: given a grading transition, it returns an AlertEvent
    (or None) with no side effects at all.
  * Dispatcher.maybe_fire() decides *whether* each channel should fire (cooldown, quiet
    hours, which channels are enabled) and then calls out to four injected callables --
    bell_fn/desktop_fn/webhook_fn/command_fn -- rather than calling curses.beep(),
    subprocess.run(), or urllib directly inline. Tests inject fake trackers for those four
    callables so no test ever actually beeps, notifies, POSTs, or execs anything for real.
    Production code gets sane defaults (module-level _default_bell_fn/_default_desktop_fn,
    plus Dispatcher._default_webhook/_default_command, which close over self.cfg) if it
    doesn't supply its own.

cfg is accepted as a plain duck-typed object exposing the [alerts] TOML keys as attributes
(bell, desktop, webhook_url, webhook_format, command, cooldown_s, quiet_hours) -- typically
a config.AlertsConfig, but this module does not import config.py, mirroring alerts.py's
choice to stay decoupled from the config loader.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from datetime import time as dtime
from typing import Any

from adsbtui.model import Aircraft, AlertLevel

#: AlertLevel -> the [alerts].events name that governs it. OVERHEAD/PASSING/OUTBOUND are
#: all "proximity" -- they're all forms of "this aircraft is/was physically close" -- while
#: INBOUND (a projected close approach) is its own "cpa" event and EMERGENCY is its own
#: "emergency" event. AlertLevel.NONE is deliberately absent: it never produces an event.
_EVENT_NAMES_BY_LEVEL = {
    AlertLevel.OVERHEAD: "proximity",
    AlertLevel.PASSING: "proximity",
    AlertLevel.OUTBOUND: "proximity",
    AlertLevel.INBOUND: "cpa",
    AlertLevel.EMERGENCY: "emergency",
}

_WEBHOOK_TIMEOUT_S = 5.0
_COMMAND_TIMEOUT_S = 5.0
_DESKTOP_TIMEOUT_S = 3.0


@dataclass
class AlertEvent:
    """One thing worth notifying about: a specific aircraft crossing into a specific
    named alert event ("proximity", "cpa", "emergency", "watchlist", ...)."""

    hex: str
    alert_level: AlertLevel
    aircraft: Aircraft
    event_name: str


def build_event(
    aircraft: Aircraft,
    alert_level: AlertLevel,
    previous_level: AlertLevel,
    events: list[str],
    event_name_override: str | None = None,
) -> AlertEvent | None:
    """Turn a grade() transition into an AlertEvent, or None if nothing should fire.

    Normal (AlertLevel-driven) path: only fires on a transition (previous_level !=
    alert_level) into a non-NONE level whose derived event name (see
    _EVENT_NAMES_BY_LEVEL) is present in the given events list.

    event_name_override path: when a caller passes event_name_override, that name is used
    directly instead of being derived from alert_level, and neither the "previous_level
    differs" nor the "alert_level is not NONE" checks apply -- only "the name is enabled in
    events" does. This lets a future watchlist integration (a match that isn't itself an
    AlertLevel transition at all) reuse this same gate instead of needing its own copy of
    the enablement/event-name-checking logic.
    """
    if event_name_override is not None:
        event_name = event_name_override
    else:
        if alert_level == previous_level or alert_level == AlertLevel.NONE:
            return None
        event_name = _EVENT_NAMES_BY_LEVEL.get(alert_level)
        if event_name is None:
            return None

    if event_name not in events:
        return None

    return AlertEvent(
        hex=aircraft.hex,
        alert_level=alert_level,
        aircraft=aircraft,
        event_name=event_name,
    )


def _event_message(event: AlertEvent) -> str:
    """Short human-readable one-liner describing an AlertEvent, shared by the desktop,
    ntfy, and Discord notification bodies."""
    ac = event.aircraft
    bits = [ac.display_flight, f"[{event.event_name}/{event.alert_level.value}]"]
    if ac.distance_mi is not None:
        bits.append(f"{ac.distance_mi:.1f}mi")
    if ac.altitude_ft is not None:
        bits.append(f"{ac.altitude_ft:.0f}ft")
    return " ".join(bits)


def _event_payload(event: AlertEvent) -> dict[str, Any]:
    """JSON-serializable dict describing an AlertEvent, for the default "json" webhook
    format."""
    ac = event.aircraft
    return {
        "hex": event.hex,
        "event": event.event_name,
        "alert_level": event.alert_level.value,
        "flight": ac.flight,
        "registration": ac.registration,
        "squawk": ac.squawk,
        "distance_mi": ac.distance_mi,
        "bearing_deg": ac.bearing_deg,
        "altitude_ft": ac.altitude_ft,
    }


def _guess_webhook_format(url: str) -> str:
    """Guess a webhook_format from the URL shape, for webhook_format="auto"."""
    lowered = url.lower()
    if "discord.com/api/webhooks" in lowered or "discordapp.com/api/webhooks" in lowered:
        return "discord"
    if "ntfy" in lowered:
        return "ntfy"
    return "json"


def _osascript_quote(text: str) -> str:
    """Quote text as an AppleScript string literal (for osascript -e)."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _default_bell_fn(event: AlertEvent) -> None:
    """Terminal bell via curses.beep(). A no-op (never raises) outside of a running
    curses session -- e.g. under pytest, or if curses isn't available at all."""
    try:
        import curses

        curses.beep()
    except Exception:
        pass


def _default_desktop_fn(event: AlertEvent) -> None:
    """Best-effort desktop notification: notify-send on Linux, osascript on macOS.
    Silently does nothing on any other platform, or if the call fails for any reason
    (missing binary, no display, timeout, ...)."""
    title = f"ADSB-TUI: {event.event_name}"
    body = _event_message(event)
    try:
        if platform.system() == "Darwin":
            script = (
                f"display notification {_osascript_quote(body)} "
                f"with title {_osascript_quote(title)}"
            )
            subprocess.run(
                ["osascript", "-e", script],
                timeout=_DESKTOP_TIMEOUT_S,
                check=False,
                capture_output=True,
            )
        else:
            subprocess.run(
                ["notify-send", title, body],
                timeout=_DESKTOP_TIMEOUT_S,
                check=False,
                capture_output=True,
            )
    except Exception:
        pass


def _parse_hhmm(text: str) -> dtime:
    """Parse an "HH:MM" string into a datetime.time. Raises ValueError on anything else,
    which callers use to treat a malformed quiet_hours string as "no quiet hours"."""
    hh_str, mm_str = text.strip().split(":", 1)
    return dtime(hour=int(hh_str), minute=int(mm_str))


def _parse_quiet_hours(quiet_hours: str) -> tuple[dtime, dtime] | None:
    """Parse "HH:MM-HH:MM" into a (start, end) pair of datetime.time, or None if
    quiet_hours is empty or malformed (malformed is treated the same as "no quiet
    hours" rather than raising, since this runs on every maybe_fire() call)."""
    if not quiet_hours:
        return None
    try:
        start_str, end_str = quiet_hours.split("-", 1)
        return _parse_hhmm(start_str), _parse_hhmm(end_str)
    except ValueError:
        return None


def _is_quiet_hours_active(quiet_hours: str, now_epoch: float) -> bool:
    """Whether now_epoch (an epoch-seconds timestamp, as returned by now_fn()) falls
    inside the quiet_hours window. Handles a window that wraps past midnight, e.g.
    "22:00-06:00"."""
    parsed = _parse_quiet_hours(quiet_hours)
    if parsed is None:
        return False
    start, end = parsed
    now_t = datetime.fromtimestamp(now_epoch).time()
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end


BellFn = Callable[[AlertEvent], None]
DesktopFn = Callable[[AlertEvent], None]
WebhookFn = Callable[[AlertEvent], None]
CommandFn = Callable[[AlertEvent], None]
NowFn = Callable[[], float]


class Dispatcher:
    """Turns AlertEvents into actual notifications, subject to per-(hex, event_name)
    cooldowns, quiet hours, and which [alerts] channels are enabled.

    bell_fn/desktop_fn/webhook_fn/command_fn are keyword-only and each default to a real
    implementation (curses.beep(), notify-send/osascript, urllib.request, subprocess.run
    respectively) but tests should inject fakes so nothing real ever fires. now_fn defaults
    to time.time but should be a fake counter in tests so cooldown/quiet-hours behavior is
    deterministic.
    """

    def __init__(
        self,
        cfg: Any,
        *,
        bell_fn: BellFn | None = None,
        desktop_fn: DesktopFn | None = None,
        webhook_fn: WebhookFn | None = None,
        command_fn: CommandFn | None = None,
        now_fn: NowFn = time.time,
    ) -> None:
        self.cfg = cfg
        self.now_fn = now_fn
        self.bell_fn: BellFn = bell_fn if bell_fn is not None else _default_bell_fn
        self.desktop_fn: DesktopFn = desktop_fn if desktop_fn is not None else _default_desktop_fn
        self.webhook_fn: WebhookFn = webhook_fn if webhook_fn is not None else self._default_webhook
        self.command_fn: CommandFn = command_fn if command_fn is not None else self._default_command
        # (hex, event_name) -> now_fn() epoch seconds at which it last actually fired.
        self._cooldowns: dict[tuple[str, str], float] = {}

    def _default_webhook(self, event: AlertEvent) -> None:
        """POST event to cfg.webhook_url, formatted per cfg.webhook_format ("auto" guesses
        json/ntfy/discord from the URL shape). Swallows all errors -- a dead webhook
        endpoint should never crash the TUI."""
        url = self.cfg.webhook_url
        if not url:
            return
        fmt = self.cfg.webhook_format
        if fmt == "auto":
            fmt = _guess_webhook_format(url)

        if fmt == "ntfy":
            body = _event_message(event).encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        elif fmt == "discord":
            body = json.dumps({"content": _event_message(event)}).encode("utf-8")
            content_type = "application/json"
        else:
            body = json.dumps(_event_payload(event)).encode("utf-8")
            content_type = "application/json"

        request = urllib.request.Request(
            url, data=body, method="POST", headers={"Content-Type": content_type}
        )
        with contextlib.suppress(urllib.error.URLError, OSError, ValueError):
            urllib.request.urlopen(request, timeout=_WEBHOOK_TIMEOUT_S)

    def _default_command(self, event: AlertEvent) -> None:
        """Run cfg.command (shell-word-split, not through a shell) with HEX/REG/CALL/
        DIST/ALT set in its environment. Swallows all errors -- a broken/missing command
        should never crash the TUI."""
        command = self.cfg.command
        if not command:
            return
        ac = event.aircraft
        env = dict(os.environ)
        env["HEX"] = event.hex
        env["REG"] = ac.registration or ""
        env["CALL"] = ac.flight or ""
        env["DIST"] = "" if ac.distance_mi is None else f"{ac.distance_mi:.2f}"
        env["ALT"] = "" if ac.altitude_ft is None else f"{ac.altitude_ft:.0f}"
        with contextlib.suppress(OSError, subprocess.SubprocessError, ValueError):
            subprocess.run(
                shlex.split(command),
                env=env,
                timeout=_COMMAND_TIMEOUT_S,
                check=False,
                capture_output=True,
            )

    def maybe_fire(self, event: AlertEvent) -> bool:
        """Fire whichever enabled channels apply to this event, subject to cooldown and
        quiet hours. Returns True if anything actually fired.

        Cooldown is per (hex, event_name): a second identical event within cfg.cooldown_s
        of the last one that actually fired is suppressed entirely (no channel is called).

        Quiet hours (cfg.quiet_hours, "HH:MM-HH:MM") suppress bell_fn and desktop_fn only
        -- webhook_fn and command_fn still fire. The idea is that bell/desktop are *local,
        in-the-room* noise (which is exactly what quiet hours should silence), while a
        webhook or command is presumed to notify somewhere else (another room, another
        device, a log) where the same quiet-hours reasoning doesn't apply.
        """
        key = (event.hex, event.event_name)
        now = self.now_fn()
        last_fired = self._cooldowns.get(key)
        if last_fired is not None and (now - last_fired) < self.cfg.cooldown_s:
            return False

        quiet = _is_quiet_hours_active(self.cfg.quiet_hours, now)

        fired = False
        if not quiet:
            if self.cfg.bell:
                self.bell_fn(event)
                fired = True
            if self.cfg.desktop:
                self.desktop_fn(event)
                fired = True
        if self.cfg.webhook_url:
            self.webhook_fn(event)
            fired = True
        if self.cfg.command:
            self.command_fn(event)
            fired = True

        if fired:
            self._cooldowns[key] = now
        return fired
