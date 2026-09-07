"""Tests for adsbtui.ui.screens.data.DataScreen -- no terminal, no network, no leaks.

The two things this screen does that are worth testing are the listing (what is on disk,
how big, how old, under what license) and the background update (exactly one at a time,
progress visible while it runs, completion handed back to the caller, failure shown rather
than raised). The updater is injected everywhere below, so nothing here downloads
anything; the fakes that block do so on an Event that every test releases in a finally,
and every screen that started a thread is joined before the test ends.
"""

from __future__ import annotations

import curses
import threading
import time

import pytest

from adsbtui.enrich.registry_update import (
    SOURCE_FAA,
    SOURCE_TAR1090,
    BuildResult,
    RegistryError,
    SourceMeta,
)
from adsbtui.ui.screens.data import (
    DataScreen,
    default_updater,
    format_age,
)

ESC = 27
DOWN = curses.KEY_DOWN
UP = curses.KEY_UP
U_LOWER = ord("u")
U_UPPER = ord("U")

DB_PATH = "/nowhere/registry.db"

#: A generous but bounded wait for a background update to reach a checkpoint. Every use is
#: an Event the fake updater sets, so this is only ever hit if something is broken.
TIMEOUT = 5.0


def meta_reader_for(**ages_and_counts: tuple[float, int]):
    """A meta reader returning one SourceMeta per source, aged relative to now.

    Each keyword is a source name mapped to (age_in_seconds, row_count).
    """

    def reader(db_path: str) -> dict[str, SourceMeta]:
        now = time.time()
        return {
            source: SourceMeta(
                source=source,
                url=None,
                fetched_at=now - age,
                row_count=count,
            )
            for source, (age, count) in ages_and_counts.items()
        }

    return reader


def render(screen: DataScreen, width: int = 100, height: int = 40) -> str:
    return "\n".join(screen.render_lines(width, height))


def line_for(screen: DataScreen, label: str, width: int = 100) -> str:
    """The one rendered line describing the named source."""
    matches = [line for line in screen.render_lines(width, 40) if label in line]
    assert matches, f"no line mentioning {label!r}"
    return matches[0]


@pytest.fixture
def screens():
    """Join every screen's update thread when the test ends, however it ends."""
    made: list[DataScreen] = []
    yield made
    for screen in made:
        screen.join(TIMEOUT)


def make_screen(screens, **kwargs) -> DataScreen:
    kwargs.setdefault("meta_reader", meta_reader_for())
    screen = DataScreen(kwargs.pop("db_path", DB_PATH), **kwargs)
    screens.append(screen)
    return screen


# --------------------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (None, "never"),
        (0, "just now"),
        (59, "just now"),
        (60, "1 minute ago"),
        (60 * 90, "1 hour ago"),
        (3600 * 5, "5 hours ago"),
        (86400, "1 day ago"),
        (86400 * 3, "3 days ago"),
        (86400 * 400, "400 days ago"),
    ],
)
def test_format_age(seconds, expected):
    assert format_age(seconds) == expected


def test_default_updater_refuses_an_unknown_source(tmp_path):
    # The two real sources would hit the network; an unknown one exercises the dispatch
    # without leaving the machine.
    with pytest.raises(RegistryError):
        default_updater("nonesuch", str(tmp_path / "registry.db"), lambda f, m: None)


# --------------------------------------------------------------------------------------
# The listing
# --------------------------------------------------------------------------------------


def test_listing_shows_rows_age_and_licensing(screens):
    screen = make_screen(
        screens,
        meta_reader=meta_reader_for(
            tar1090=(86400 * 3, 612431),
            faa=(3600 * 2, 291504),
        ),
    )

    assert screen.title == "Data"

    tar_line = line_for(screen, "tar1090-db")
    assert "612,431 rows" in tar_line
    assert "3 days ago" in tar_line
    assert "non-commercial use only" in tar_line

    faa_line = line_for(screen, "FAA")
    assert "291,504 rows" in faa_line
    assert "2 hours ago" in faa_line
    assert "public domain" in faa_line

    assert "u: update" in render(screen)


def test_absent_source_is_reported_as_not_downloaded(screens):
    screen = make_screen(screens, meta_reader=meta_reader_for(tar1090=(60, 10)))
    assert "not downloaded" in line_for(screen, "FAA")
    assert screen.status_for(SOURCE_FAA).present is False
    assert screen.status_for(SOURCE_TAR1090).present is True


def test_only_the_two_downloadable_registries_are_listed(screens):
    # The screen's whole job is fetching registries, so every row on it must be one this
    # screen can actually go and fetch. Since the legacy user-supplied CSV was removed
    # that now holds structurally -- there is no "listed but un-fetchable" source left,
    # and update_all() may fan out over every spec without filtering.
    screen = make_screen(screens)
    assert [spec.key for spec in screen.specs] == [SOURCE_TAR1090, SOURCE_FAA]
    assert screen.update_all() is True


def test_old_source_is_flagged_stale(screens):
    screen = make_screen(
        screens,
        stale_after_days=30.0,
        meta_reader=meta_reader_for(tar1090=(86400 * 45, 100), faa=(86400 * 2, 100)),
    )
    assert "(stale)" in line_for(screen, "tar1090-db")
    assert "(stale)" not in line_for(screen, "FAA")
    assert screen.status_for(SOURCE_TAR1090).stale is True


def test_staleness_threshold_is_configurable(screens):
    screen = make_screen(
        screens,
        stale_after_days=1.0,
        meta_reader=meta_reader_for(faa=(86400 * 2, 100)),
    )
    assert screen.status_for(SOURCE_FAA).stale is True


def test_up_and_down_move_the_selection(screens):
    screen = make_screen(screens)
    assert screen.selected_spec.key == SOURCE_TAR1090
    assert screen.handle_key(DOWN) is None
    assert screen.selected_spec.key == SOURCE_FAA
    screen.handle_key(UP)
    assert screen.selected_spec.key == SOURCE_TAR1090


def test_escape_closes(screens):
    assert make_screen(screens).handle_key(ESC) == "close"


def test_render_lines_is_clipped_and_bounded(screens):
    screen = make_screen(screens, meta_reader=meta_reader_for(tar1090=(60, 5)))
    lines = screen.render_lines(30, 6)
    assert len(lines) <= 6
    assert all(len(line) <= 30 for line in lines)
    assert screen.render_lines(0, 10) == []
    assert screen.render_lines(40, 0) == []


# --------------------------------------------------------------------------------------
# Updating
# --------------------------------------------------------------------------------------


class BlockingUpdater:
    """A fake updater that reports progress, then waits to be released by the test."""

    def __init__(self, fraction: float = 0.4, row_count: int = 1234) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []
        self._fraction = fraction
        self._row_count = row_count

    def __call__(self, source, db_path, progress_fn) -> BuildResult:
        self.calls.append(source)
        progress_fn(self._fraction, f"downloading {source}")
        self.reached.set()
        assert self.release.wait(TIMEOUT), "test never released the fake updater"
        return BuildResult(source=source, db_path=db_path, row_count=self._row_count)


def test_u_starts_one_update_and_a_second_is_refused(screens):
    updater = BlockingUpdater()
    screen = make_screen(screens, updater=updater)
    try:
        screen.handle_key(U_LOWER)
        assert updater.reached.wait(TIMEOUT)
        assert screen.is_updating is True
        assert screen.running_source == SOURCE_TAR1090

        # A second 'u' while the first is in flight must not start anything.
        screen.handle_key(U_LOWER)
        screen.handle_key(U_UPPER)
        assert "an update is already running" in render(screen)
        assert updater.calls == [SOURCE_TAR1090]
    finally:
        updater.release.set()
        screen.join(TIMEOUT)

    assert screen.is_updating is False
    assert updater.calls == [SOURCE_TAR1090]


def test_progress_is_reflected_in_render_lines(screens):
    updater = BlockingUpdater(fraction=0.4)
    screen = make_screen(screens, updater=updater)
    try:
        screen.handle_key(U_LOWER)
        assert updater.reached.wait(TIMEOUT)
        text = render(screen)
        assert screen.progress == pytest.approx(0.4)
        assert "40%" in text
        assert "#" in text
        assert "downloading tar1090" in text
    finally:
        updater.release.set()
        screen.join(TIMEOUT)


def test_completion_is_reported_to_the_caller_and_refreshes_the_listing(screens):
    counts = iter([{}, {SOURCE_TAR1090: (60.0, 4242)}])

    def meta_reader(db_path):
        entries = next(counts, {SOURCE_TAR1090: (60.0, 4242)})
        now = time.time()
        return {
            source: SourceMeta(source=source, url=None, fetched_at=now - age, row_count=count)
            for source, (age, count) in entries.items()
        }

    def updater(source, db_path, progress_fn):
        progress_fn(1.0, f"{source}: done")
        return BuildResult(source=source, db_path=db_path, row_count=4242)

    screen = make_screen(screens, updater=updater, meta_reader=meta_reader)
    assert screen.take_finished() == []
    assert "not downloaded" in line_for(screen, "tar1090-db")

    screen.handle_key(U_LOWER)
    screen.join(TIMEOUT)

    outcomes = screen.take_finished()
    assert len(outcomes) == 1
    assert outcomes[0].source == SOURCE_TAR1090
    assert outcomes[0].ok is True
    assert outcomes[0].row_count == 4242
    # Draining is one-shot: the caller reloads its enricher once, not on every poll.
    assert screen.take_finished() == []

    assert "4,242 rows" in line_for(screen, "tar1090-db")
    assert "4,242 rows" in render(screen)


def test_update_all_runs_every_downloadable_source_in_one_thread(screens):
    seen: list[str] = []

    def updater(source, db_path, progress_fn):
        seen.append(source)
        progress_fn(0.5, "working")
        return BuildResult(source=source, db_path=db_path, row_count=7)

    screen = make_screen(screens, updater=updater)
    screen.handle_key(U_UPPER)
    screen.join(TIMEOUT)

    assert seen == [SOURCE_TAR1090, SOURCE_FAA]
    assert [outcome.source for outcome in screen.take_finished()] == [
        SOURCE_TAR1090,
        SOURCE_FAA,
    ]


def test_a_failing_update_surfaces_the_error_instead_of_raising(screens):
    def updater(source, db_path, progress_fn):
        progress_fn(0.1, "downloading")
        raise RegistryError("could not download: HTTP 503 Service Unavailable")

    screen = make_screen(screens, updater=updater)
    screen.handle_key(U_LOWER)
    screen.join(TIMEOUT)

    text = render(screen)
    assert "Error:" in text
    assert "HTTP 503" in text

    outcomes = screen.take_finished()
    assert len(outcomes) == 1
    assert outcomes[0].ok is False
    assert outcomes[0].row_count is None
    assert "HTTP 503" in outcomes[0].message
    assert screen.is_updating is False


def test_a_failing_update_does_not_stop_the_remaining_sources(screens):
    def updater(source, db_path, progress_fn):
        if source == SOURCE_TAR1090:
            raise RegistryError("boom")
        return BuildResult(source=source, db_path=db_path, row_count=3)

    screen = make_screen(screens, updater=updater)
    screen.handle_key(U_UPPER)
    screen.join(TIMEOUT)

    outcomes = screen.take_finished()
    assert [(o.source, o.ok) for o in outcomes] == [(SOURCE_TAR1090, False), (SOURCE_FAA, True)]


def test_an_unchanged_source_says_so(screens):
    def updater(source, db_path, progress_fn):
        return BuildResult(source=source, db_path=db_path, row_count=99, not_modified=True)

    screen = make_screen(screens, updater=updater)
    screen.handle_key(U_LOWER)
    screen.join(TIMEOUT)

    outcome = screen.take_finished()[0]
    assert outcome.ok is True
    assert outcome.not_modified is True
    assert "already up to date" in render(screen)


def test_a_second_update_is_allowed_once_the_first_has_finished(screens):
    calls: list[str] = []

    def updater(source, db_path, progress_fn):
        calls.append(source)
        return BuildResult(source=source, db_path=db_path, row_count=1)

    screen = make_screen(screens, updater=updater)
    screen.handle_key(U_LOWER)
    screen.join(TIMEOUT)
    screen.handle_key(U_LOWER)
    screen.join(TIMEOUT)

    assert calls == [SOURCE_TAR1090, SOURCE_TAR1090]
    assert "an update is already running" not in render(screen)
