"""Cross-snapshot aircraft tracking: first-seen/last-seen bookkeeping, staleness, and the
"lingering" of aircraft that briefly drop out of a feed.

This module is pure: no curses calls, no network calls, no global state, and no calls to
time.time() -- the caller always passes `now` explicitly, which is what makes update_tracks()
trivially unit-testable without a clock or a terminal.
"""

from __future__ import annotations

from dataclasses import replace

from adsbtui.model import Aircraft

#: An aircraft counts as "new" while (now - first_seen) is under this many seconds. Fixed
#: and documented here rather than threaded through as a parameter, since every caller in
#: this build wants the same window and a config knob would be one more untested surface.
NEW_WINDOW_S = 30.0


def update_tracks(
    previous: dict[str, Aircraft],
    current_batch: dict[str, Aircraft],
    now: float,
    stale_after_s: float,
    linger_s: float,
) -> dict[str, Aircraft]:
    """Merge this tick's parsed aircraft with the previous tick's tracked state.

    `previous` is the tracked-by-hex dict this function returned last tick (or {} on the
    first tick). `current_batch` is this tick's freshly parsed aircraft, keyed by hex --
    these objects' first_seen/last_seen/is_new/is_stale/is_lost fields are ignored on input
    and recomputed here.

    Returns a NEW dict (neither input is mutated):

      * Every hex present in current_batch is kept, with:
          - first_seen carried over from previous[hex].first_seen if that hex was already
            tracked, else set to `now` (this is its first tick).
          - last_seen set to `now`.
          - is_stale = aircraft.seen_pos_s is not None and aircraft.seen_pos_s > stale_after_s.
          - is_lost = False (it is, by definition, present this tick).
          - is_new = (now - first_seen) < NEW_WINDOW_S.

      * Every hex present in `previous` but NOT in `current_batch` is carried forward with
        is_lost = True (its other derived fields -- distance, bearing, etc. -- are left as
        they were on its last real update, since there is no new data to recompute them
        from) as long as (now - previous[hex].last_seen) <= linger_s; once that linger
        window has passed, the hex is dropped from the result entirely.

    Neither `previous` nor `current_batch` (nor the Aircraft objects inside them) is
    mutated -- every Aircraft placed into the returned dict is a fresh copy (via
    dataclasses.replace), so the caller's own references stay exactly as they were.
    """
    result: dict[str, Aircraft] = {}

    for hex_id, aircraft in current_batch.items():
        prior = previous.get(hex_id)
        first_seen = prior.first_seen if prior is not None and prior.first_seen is not None else now
        is_stale = aircraft.seen_pos_s is not None and aircraft.seen_pos_s > stale_after_s
        is_new = (now - first_seen) < NEW_WINDOW_S

        result[hex_id] = replace(
            aircraft,
            first_seen=first_seen,
            last_seen=now,
            is_stale=is_stale,
            is_lost=False,
            is_new=is_new,
        )

    for hex_id, prior in previous.items():
        if hex_id in result:
            continue
        last_seen = prior.last_seen if prior.last_seen is not None else now
        if (now - last_seen) > linger_s:
            continue
        result[hex_id] = replace(prior, is_lost=True)

    return result
