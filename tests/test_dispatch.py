"""Tests for adsbtui.dispatch: alert event construction and notification dispatch.

All four notification channels (bell/desktop/webhook/command) are replaced with fake
list-appending trackers in every test -- nothing here ever beeps, pops a real desktop
notification, opens a socket, or execs a real process. now_fn is a fake mutable clock so
cooldown and quiet-hours behavior is deterministic and doesn't depend on wall-clock time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adsbtui.dispatch import AlertEvent, Dispatcher, build_event
from adsbtui.model import Aircraft, AlertLevel

DEFAULT_EVENTS = ["proximity", "cpa", "watchlist", "emergency"]


def make_aircraft(**overrides) -> Aircraft:
    defaults = dict(hex="a1b2c3", is_icao=True, flight="UAL123", registration="N123AB")
    defaults.update(overrides)
    return Aircraft(**defaults)


@dataclass
class FakeAlertsConfig:
    """A minimal duck-typed stand-in for config.AlertsConfig -- dispatch.py doesn't
    import config.py, so any object with these attributes works."""

    bell: bool = True
    desktop: bool = False
    webhook_url: str = ""
    webhook_format: str = "auto"
    command: str = ""
    cooldown_s: float = 600.0
    quiet_hours: str = ""
    squawks: list[int] = field(default_factory=lambda: [7500, 7600, 7700])


class FakeClock:
    """A mutable epoch-seconds counter usable as Dispatcher's now_fn."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def set_time_of_day(self, hh: int, mm: int) -> None:
        """Move the clock to hh:mm on the same local day it's currently on, so
        quiet-hours tests can land on an exact wall-clock time without depending on
        what start value was chosen."""
        import datetime as _dt

        current = _dt.datetime.fromtimestamp(self.now)
        target = current.replace(hour=hh, minute=mm, second=0, microsecond=0)
        self.now = target.timestamp()


def make_trackers() -> dict[str, list[AlertEvent]]:
    return {"bell": [], "desktop": [], "webhook": [], "command": []}


def make_dispatcher(cfg, clock, trackers) -> Dispatcher:
    return Dispatcher(
        cfg,
        bell_fn=lambda event: trackers["bell"].append(event),
        desktop_fn=lambda event: trackers["desktop"].append(event),
        webhook_fn=lambda event: trackers["webhook"].append(event),
        command_fn=lambda event: trackers["command"].append(event),
        now_fn=clock,
    )


class TestBuildEvent:
    def test_no_event_when_level_unchanged(self):
        ac = make_aircraft()
        assert (
            build_event(ac, AlertLevel.OVERHEAD, AlertLevel.OVERHEAD, DEFAULT_EVENTS)
            is None
        )

    def test_no_event_when_new_level_is_none(self):
        ac = make_aircraft()
        assert (
            build_event(ac, AlertLevel.NONE, AlertLevel.OVERHEAD, DEFAULT_EVENTS) is None
        )

    def test_overhead_transition_maps_to_proximity(self):
        ac = make_aircraft()
        event = build_event(ac, AlertLevel.OVERHEAD, AlertLevel.NONE, DEFAULT_EVENTS)
        assert event is not None
        assert event.event_name == "proximity"
        assert event.alert_level == AlertLevel.OVERHEAD
        assert event.hex == ac.hex
        assert event.aircraft is ac

    def test_passing_and_outbound_also_map_to_proximity(self):
        ac = make_aircraft()
        for level in (AlertLevel.PASSING, AlertLevel.OUTBOUND):
            event = build_event(ac, level, AlertLevel.NONE, DEFAULT_EVENTS)
            assert event is not None
            assert event.event_name == "proximity"

    def test_inbound_maps_to_cpa(self):
        ac = make_aircraft()
        event = build_event(ac, AlertLevel.INBOUND, AlertLevel.NONE, DEFAULT_EVENTS)
        assert event is not None
        assert event.event_name == "cpa"

    def test_emergency_maps_to_emergency(self):
        ac = make_aircraft()
        event = build_event(ac, AlertLevel.EMERGENCY, AlertLevel.NONE, DEFAULT_EVENTS)
        assert event is not None
        assert event.event_name == "emergency"

    def test_disabled_event_name_suppresses_event(self):
        ac = make_aircraft()
        events = ["cpa", "watchlist", "emergency"]  # "proximity" not enabled
        assert build_event(ac, AlertLevel.OVERHEAD, AlertLevel.NONE, events) is None

    def test_transition_between_two_non_none_levels_still_fires(self):
        # OVERHEAD -> EMERGENCY is still a transition even though neither side is NONE.
        ac = make_aircraft()
        event = build_event(ac, AlertLevel.EMERGENCY, AlertLevel.OVERHEAD, DEFAULT_EVENTS)
        assert event is not None
        assert event.event_name == "emergency"

    def test_override_bypasses_transition_and_none_checks(self):
        # Same level on both sides (no AlertLevel transition at all), and the new level is
        # NONE -- normally both would suppress the event -- but the override name is used
        # directly and neither check applies.
        ac = make_aircraft()
        event = build_event(
            ac,
            AlertLevel.NONE,
            AlertLevel.NONE,
            DEFAULT_EVENTS,
            event_name_override="watchlist",
        )
        assert event is not None
        assert event.event_name == "watchlist"
        assert event.alert_level == AlertLevel.NONE

    def test_override_still_gated_by_events_list(self):
        ac = make_aircraft()
        event = build_event(
            ac,
            AlertLevel.NONE,
            AlertLevel.NONE,
            ["proximity", "cpa", "emergency"],  # no "watchlist"
            event_name_override="watchlist",
        )
        assert event is None


class TestMaybeFireChannels:
    def test_bell_only_when_enabled(self):
        cfg = FakeAlertsConfig(bell=True, desktop=False)
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == [event]
        assert trackers["desktop"] == []
        assert trackers["webhook"] == []
        assert trackers["command"] == []

    def test_nothing_enabled_means_no_fire(self):
        cfg = FakeAlertsConfig(bell=False, desktop=False, webhook_url="", command="")
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is False
        assert all(track == [] for track in trackers.values())

    def test_all_four_channels_fire_with_sane_arguments(self):
        cfg = FakeAlertsConfig(
            bell=True,
            desktop=True,
            webhook_url="https://example.com/hook",
            command="/usr/local/bin/notify",
        )
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        ac = make_aircraft(hex="deadbe", flight="SWA42", distance_mi=3.2, altitude_ft=5500.0)
        event = AlertEvent(
            hex=ac.hex, alert_level=AlertLevel.INBOUND, aircraft=ac, event_name="cpa"
        )

        assert dispatcher.maybe_fire(event) is True
        for channel in ("bell", "desktop", "webhook", "command"):
            assert trackers[channel] == [event], channel
            fired_event = trackers[channel][0]
            assert fired_event.hex == "deadbe"
            assert fired_event.aircraft.flight == "SWA42"
            assert fired_event.event_name == "cpa"


class TestCooldown:
    def test_second_identical_event_within_cooldown_is_suppressed(self):
        cfg = FakeAlertsConfig(bell=True, cooldown_s=600.0)
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        clock.advance(100.0)  # well within the 600s cooldown
        assert dispatcher.maybe_fire(event) is False
        assert trackers["bell"] == [event]  # only fired once

    def test_event_fires_again_after_cooldown_elapses(self):
        cfg = FakeAlertsConfig(bell=True, cooldown_s=600.0)
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        clock.advance(600.0)  # exactly at the cooldown boundary: allowed again
        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == [event, event]

    def test_cooldown_is_independent_per_hex_and_event_name(self):
        cfg = FakeAlertsConfig(bell=True, cooldown_s=600.0)
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        ac_a = make_aircraft(hex="aaaaaa")
        ac_b = make_aircraft(hex="bbbbbb")
        event_a_proximity = AlertEvent(
            hex="aaaaaa", alert_level=AlertLevel.OVERHEAD, aircraft=ac_a, event_name="proximity"
        )
        event_a_cpa = AlertEvent(
            hex="aaaaaa", alert_level=AlertLevel.INBOUND, aircraft=ac_a, event_name="cpa"
        )
        event_b_proximity = AlertEvent(
            hex="bbbbbb", alert_level=AlertLevel.OVERHEAD, aircraft=ac_b, event_name="proximity"
        )

        assert dispatcher.maybe_fire(event_a_proximity) is True
        # Different event name, same hex -- not suppressed by A's proximity cooldown.
        assert dispatcher.maybe_fire(event_a_cpa) is True
        # Different hex, same event name -- not suppressed either.
        assert dispatcher.maybe_fire(event_b_proximity) is True
        assert len(trackers["bell"]) == 3

    def test_suppressed_event_does_not_reset_the_cooldown_clock(self):
        cfg = FakeAlertsConfig(bell=True, cooldown_s=600.0)
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True  # t=0, fires
        clock.advance(300.0)
        assert dispatcher.maybe_fire(event) is False  # t=300, suppressed
        clock.advance(300.0)  # t=600 relative to the fire at t=0, not the suppressed try
        assert dispatcher.maybe_fire(event) is True
        assert len(trackers["bell"]) == 2


class TestQuietHours:
    def test_quiet_hours_suppress_bell_and_desktop(self):
        cfg = FakeAlertsConfig(
            bell=True,
            desktop=True,
            webhook_url="https://example.com/hook",
            command="/usr/local/bin/notify",
            quiet_hours="22:00-06:00",
        )
        clock = FakeClock()
        clock.set_time_of_day(23, 0)  # inside the window
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == []
        assert trackers["desktop"] == []
        assert trackers["webhook"] == [event]
        assert trackers["command"] == [event]

    def test_quiet_hours_window_wraps_past_midnight_early_morning(self):
        # bell is the only channel enabled, and it's suppressed by quiet hours, so nothing
        # fires at all -- maybe_fire() should report that honestly rather than claim True.
        cfg = FakeAlertsConfig(bell=True, quiet_hours="22:00-06:00")
        clock = FakeClock()
        clock.set_time_of_day(3, 30)  # inside the window, after midnight
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is False
        assert trackers["bell"] == []

    def test_outside_quiet_hours_everything_fires(self):
        cfg = FakeAlertsConfig(
            bell=True,
            desktop=True,
            webhook_url="https://example.com/hook",
            command="/usr/local/bin/notify",
            quiet_hours="22:00-06:00",
        )
        clock = FakeClock()
        clock.set_time_of_day(12, 0)  # well outside the window
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == [event]
        assert trackers["desktop"] == [event]
        assert trackers["webhook"] == [event]
        assert trackers["command"] == [event]

    def test_empty_quiet_hours_never_suppresses(self):
        cfg = FakeAlertsConfig(bell=True, quiet_hours="")
        clock = FakeClock()
        clock.set_time_of_day(2, 0)  # would be "quiet" under a wrapping window, but none is set
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == [event]

    def test_malformed_quiet_hours_is_treated_as_no_quiet_hours(self):
        cfg = FakeAlertsConfig(bell=True, quiet_hours="not-a-range")
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)
        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.OVERHEAD,
            aircraft=make_aircraft(),
            event_name="proximity",
        )

        assert dispatcher.maybe_fire(event) is True
        assert trackers["bell"] == [event]


class TestDefaultCallablesAreNotUsedWhenFakesProvided:
    def test_dispatcher_never_touches_real_channels_when_fakes_given(self):
        # A regression guard: as long as all four *_fn are passed explicitly, Dispatcher
        # must never fall back to its real defaults, even when webhook_url/command look
        # like they'd trigger real network/process activity.
        cfg = FakeAlertsConfig(
            bell=True,
            desktop=True,
            webhook_url="https://example.invalid/should-never-be-hit",
            command="/bin/should/never/run --with-args",
        )
        clock = FakeClock()
        trackers = make_trackers()
        dispatcher = make_dispatcher(cfg, clock, trackers)

        assert dispatcher.bell_fn is not None
        assert dispatcher.webhook_fn is not dispatcher._default_webhook
        assert dispatcher.command_fn is not dispatcher._default_command

        event = AlertEvent(
            hex="a1b2c3",
            alert_level=AlertLevel.EMERGENCY,
            aircraft=make_aircraft(),
            event_name="emergency",
        )
        assert dispatcher.maybe_fire(event) is True
        assert len(trackers["webhook"]) == 1
        assert len(trackers["command"]) == 1


class TestDefaultDispatcherConstruction:
    def test_default_fns_are_used_when_none_injected(self):
        # No fakes at all: Dispatcher should fall back to its module-level/bound defaults
        # rather than raising. We don't invoke maybe_fire() here (that would risk a real
        # beep/notification/subprocess) -- just confirm construction wires up the defaults.
        cfg = FakeAlertsConfig()
        dispatcher = Dispatcher(cfg)
        assert dispatcher.bell_fn is not None
        assert dispatcher.desktop_fn is not None
        assert dispatcher.webhook_fn == dispatcher._default_webhook
        assert dispatcher.command_fn == dispatcher._default_command
