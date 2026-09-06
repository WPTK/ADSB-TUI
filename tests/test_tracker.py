"""Tests for adsbtui.tracker: pure cross-snapshot aircraft tracking (first/last seen,
staleness, and the "lingering" of aircraft that briefly drop out of a feed)."""

from __future__ import annotations

from adsbtui.model import Aircraft
from adsbtui.tracker import NEW_WINDOW_S, update_tracks

DEFAULT_STALE_AFTER_S = 15.0
DEFAULT_LINGER_S = 30.0


def make_aircraft(hex_id: str = "a1b2c3", **overrides) -> Aircraft:
    """Build a minimal freshly-"parsed" Aircraft with sane defaults, overridden per test.
    Mirrors what normalize.parse_aircraft() would return for this tick -- first_seen/
    last_seen/is_new/is_stale/is_lost are not yet meaningful and update_tracks() is
    expected to (re)compute all of them regardless of what's passed in here."""
    defaults = dict(hex=hex_id, is_icao=True)
    defaults.update(overrides)
    return Aircraft(**defaults)


class TestFirstSeen:
    def test_new_hex_gets_first_seen_equal_to_now(self):
        now = 1000.0
        current = {"a1b2c3": make_aircraft()}
        result = update_tracks({}, current, now, DEFAULT_STALE_AFTER_S, DEFAULT_LINGER_S)

        assert result["a1b2c3"].first_seen == now
        assert result["a1b2c3"].last_seen == now

    def test_persisting_hex_keeps_original_first_seen(self):
        first_tick_now = 1000.0
        previous = update_tracks(
            {}, {"a1b2c3": make_aircraft()}, first_tick_now, DEFAULT_STALE_AFTER_S, DEFAULT_LINGER_S
        )
        assert previous["a1b2c3"].first_seen == first_tick_now

        second_tick_now = 1005.0
        result = update_tracks(
            previous,
            {"a1b2c3": make_aircraft()},
            second_tick_now,
            DEFAULT_STALE_AFTER_S,
            DEFAULT_LINGER_S,
        )

        assert result["a1b2c3"].first_seen == first_tick_now
        assert result["a1b2c3"].last_seen == second_tick_now

    def test_first_seen_carried_over_across_many_ticks(self):
        first_tick_now = 500.0
        state = update_tracks(
            {}, {"abcdef": make_aircraft(hex_id="abcdef")}, first_tick_now, 15.0, 30.0
        )
        for tick in range(1, 6):
            now = first_tick_now + tick * 5.0
            state = update_tracks(
                state, {"abcdef": make_aircraft(hex_id="abcdef")}, now, 15.0, 30.0
            )
        assert state["abcdef"].first_seen == first_tick_now
        assert state["abcdef"].last_seen == first_tick_now + 5 * 5.0

    def test_does_not_mutate_previous_or_current_batch_aircraft_objects(self):
        # update_tracks must be pure: it must not mutate the Aircraft objects passed in,
        # only return fresh copies in the new dict.
        prev_ac = make_aircraft()
        prev_ac.first_seen = 100.0
        prev_ac.last_seen = 100.0
        previous = {"a1b2c3": prev_ac}

        current_ac = make_aircraft()
        current_batch = {"a1b2c3": current_ac}

        update_tracks(previous, current_batch, 200.0, DEFAULT_STALE_AFTER_S, DEFAULT_LINGER_S)

        assert prev_ac.first_seen == 100.0
        assert prev_ac.last_seen == 100.0
        assert prev_ac.is_lost is False
        assert current_ac.first_seen is None
        assert current_ac.last_seen is None


class TestIsNew:
    def test_just_appeared_is_new(self):
        now = 1000.0
        result = update_tracks(
            {}, {"a1b2c3": make_aircraft()}, now, DEFAULT_STALE_AFTER_S, DEFAULT_LINGER_S
        )
        assert result["a1b2c3"].is_new is True

    def test_still_within_new_window_is_new(self):
        first_tick_now = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, first_tick_now, 15.0, 30.0)

        later_now = first_tick_now + (NEW_WINDOW_S - 1.0)
        result = update_tracks(previous, {"a1b2c3": make_aircraft()}, later_now, 15.0, 30.0)
        assert result["a1b2c3"].is_new is True

    def test_past_new_window_is_no_longer_new(self):
        first_tick_now = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, first_tick_now, 15.0, 30.0)

        later_now = first_tick_now + NEW_WINDOW_S
        result = update_tracks(previous, {"a1b2c3": make_aircraft()}, later_now, 15.0, 30.0)
        assert result["a1b2c3"].is_new is False

    def test_well_past_new_window_is_not_new(self):
        first_tick_now = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, first_tick_now, 15.0, 30.0)

        later_now = first_tick_now + 3600.0
        result = update_tracks(previous, {"a1b2c3": make_aircraft()}, later_now, 15.0, 30.0)
        assert result["a1b2c3"].is_new is False


class TestIsLostAndLinger:
    def test_disappearing_hex_is_marked_lost_within_linger_window(self):
        now1 = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, now1, 15.0, 30.0)

        now2 = 1005.0
        result = update_tracks(previous, {}, now2, 15.0, 30.0)

        assert "a1b2c3" in result
        assert result["a1b2c3"].is_lost is True

    def test_stays_lost_across_successive_ticks_within_linger(self):
        now1 = 1000.0
        state = update_tracks({}, {"a1b2c3": make_aircraft()}, now1, 15.0, 30.0)

        now2 = 1010.0
        state = update_tracks(state, {}, now2, 15.0, 30.0)
        assert state["a1b2c3"].is_lost is True

        now3 = 1020.0
        state = update_tracks(state, {}, now3, 15.0, 30.0)
        assert "a1b2c3" in state
        assert state["a1b2c3"].is_lost is True

    def test_dropped_entirely_once_linger_window_passes(self):
        now1 = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, now1, 15.0, 30.0)
        last_seen = previous["a1b2c3"].last_seen

        now2 = last_seen + 30.0  # exactly at the linger boundary: still kept
        result = update_tracks(previous, {}, now2, 15.0, 30.0)
        assert "a1b2c3" in result
        assert result["a1b2c3"].is_lost is True

        now3 = last_seen + 30.01  # just past the linger boundary: dropped
        result2 = update_tracks(result, {}, now3, 15.0, 30.0)
        assert "a1b2c3" not in result2

    def test_reappearing_before_linger_expires_clears_lost_and_keeps_first_seen(self):
        now1 = 1000.0
        previous = update_tracks({}, {"a1b2c3": make_aircraft()}, now1, 15.0, 30.0)

        now2 = 1010.0
        lost_state = update_tracks(previous, {}, now2, 15.0, 30.0)
        assert lost_state["a1b2c3"].is_lost is True

        now3 = 1015.0
        result = update_tracks(lost_state, {"a1b2c3": make_aircraft()}, now3, 15.0, 30.0)

        assert result["a1b2c3"].is_lost is False
        assert result["a1b2c3"].first_seen == now1
        assert result["a1b2c3"].last_seen == now3

    def test_multiple_independent_hexes_tracked_separately(self):
        now1 = 1000.0
        previous = update_tracks(
            {},
            {"aaaaaa": make_aircraft(hex_id="aaaaaa"), "bbbbbb": make_aircraft(hex_id="bbbbbb")},
            now1,
            15.0,
            30.0,
        )

        now2 = 1010.0
        # "aaaaaa" disappears, "bbbbbb" persists.
        result = update_tracks(
            previous, {"bbbbbb": make_aircraft(hex_id="bbbbbb")}, now2, 15.0, 30.0
        )

        assert result["aaaaaa"].is_lost is True
        assert result["bbbbbb"].is_lost is False
        assert result["bbbbbb"].first_seen == now1


class TestIsStale:
    def test_seen_pos_under_threshold_is_not_stale(self):
        now = 1000.0
        ac = make_aircraft(seen_pos_s=5.0)
        result = update_tracks({}, {"a1b2c3": ac}, now, stale_after_s=15.0, linger_s=30.0)
        assert result["a1b2c3"].is_stale is False

    def test_seen_pos_exactly_at_threshold_is_not_stale(self):
        # is_stale is strictly-greater-than, per spec: "seen_pos_s > stale_after_s".
        now = 1000.0
        ac = make_aircraft(seen_pos_s=15.0)
        result = update_tracks({}, {"a1b2c3": ac}, now, stale_after_s=15.0, linger_s=30.0)
        assert result["a1b2c3"].is_stale is False

    def test_seen_pos_over_threshold_is_stale(self):
        now = 1000.0
        ac = make_aircraft(seen_pos_s=15.1)
        result = update_tracks({}, {"a1b2c3": ac}, now, stale_after_s=15.0, linger_s=30.0)
        assert result["a1b2c3"].is_stale is True

    def test_no_seen_pos_at_all_is_not_stale(self):
        now = 1000.0
        ac = make_aircraft(seen_pos_s=None)
        result = update_tracks({}, {"a1b2c3": ac}, now, stale_after_s=15.0, linger_s=30.0)
        assert result["a1b2c3"].is_stale is False

    def test_staleness_is_recomputed_every_tick_from_the_new_batch(self):
        now1 = 1000.0
        state = update_tracks(
            {}, {"a1b2c3": make_aircraft(seen_pos_s=100.0)}, now1, stale_after_s=15.0, linger_s=30.0
        )
        assert state["a1b2c3"].is_stale is True

        now2 = 1005.0
        state = update_tracks(
            state,
            {"a1b2c3": make_aircraft(seen_pos_s=1.0)},
            now2,
            stale_after_s=15.0,
            linger_s=30.0,
        )
        assert state["a1b2c3"].is_stale is False
