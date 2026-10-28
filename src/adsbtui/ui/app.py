"""The curses event loop: background network polling on a daemon thread, and a main thread
that only ever drains a queue and draws -- it never does I/O itself.

This is deliberately the most defensively-coded file in the project. Every one of the
numbered requirements in the module-level docstrings below fixes a specific, verified bug
in the original tool:

  1. curses.curs_set(0) is wrapped in try/except curses.error -- some terminals do not
     support cursor visibility control, and that must not crash startup.
  2. stdscr.timeout(<=250ms) makes getch() block briefly and return -1 on timeout, so the
     quit key is responsive immediately and the process idles instead of busy-polling.
  3. All networking runs on a background daemon thread; the fetch thread NEVER calls any
     curses function, so a network hiccup can never corrupt or block the display.
  4. The main thread only drains a bounded queue and draws -- it never blocks on the
     network -- and only repaints when a `dirty` flag is set, instead of clearing/redrawing
     every tick regardless of whether anything changed.
  5. curses.KEY_RESIZE re-runs the draw against the new stdscr.getmaxyx().
  6. Every stdscr.addstr() call is individually guarded against curses.error (writing to the
     bottom-right cell, or a too-narrow/short terminal, must never crash the program); below
     a sane minimum size the normal layout is skipped for a single centered message.
  7. Colors/attributes are resolved from ui.theme's semantic row styles ONCE at startup,
     with start_color()/use_default_colors() guarded and a bold/reverse-only fallback when
     colors are unavailable or disabled.
  8. q/F10 exits cleanly (the fetch thread is joined via a stop Event); p toggles pause;
     +/- adjust the filter radius live; a raw KeyboardInterrupt (Ctrl-C) is caught around the
     whole run() body instead of being allowed to dump a traceback through curses.
"""

from __future__ import annotations

import contextlib
import curses
import logging
import os
import queue
import sqlite3
import threading
import time
from collections.abc import Callable
from typing import Any

from adsbtui import (
    config_write,
    dispatch,
    enrich,
    normalize,
    output,
    pipeline,
)
from adsbtui import watchlist as watchlist_mod
from adsbtui.config import Config, default_config_path, layer_for_save
from adsbtui.enrich import registry_update
from adsbtui.model import Aircraft
from adsbtui.sources import Source, SourceError
from adsbtui.ui.controller import Controller, Outcome
from adsbtui.ui.frame import Frame
from adsbtui.ui.render import Layout, Renderer, layout_for
from adsbtui.ui.sorting import sort_aircraft
from adsbtui.ui.view import ViewState

#: getch() timeout in milliseconds. Must be <= 250 per the task's requirement so the UI
#: stays responsive to the quit key; kept comfortably under that ceiling.
GETCH_TIMEOUT_MS = 200

#: The first term (and the reset value) of the fetch thread's exponential backoff sequence
#: (1, 2, 4, 8, ... capped at cfg.source.backoff_max_s) used after a failed fetch.
BACKOFF_BASE_S = 1.0

_LOGGER_NAME = "adsbtui"


def run_check(cfg: Config, source: Source, clock: Callable[[], float] = time.time) -> int:
    """Non-curses helper for a CLI --check flag: fetch once, parse, and report.

    Prints "OK <n> aircraft" and returns 0 on success. On any Source-family error, prints
    "ERROR: <message>" and returns 4. Never touches curses.
    """
    try:
        raw = source.fetch(cfg.source.timeout_s)
    except SourceError as exc:
        print(f"ERROR: {exc}")
        return 4

    snapshot = normalize.parse_snapshot(raw, clock())
    count = sum(
        1 for raw_ac in snapshot.raw_aircraft if normalize.parse_aircraft(raw_ac) is not None
    )
    print(f"OK {count} aircraft")
    return 0


class App:
    """Owns the fetch thread, the shared queue, and the curses draw loop."""

    def __init__(
        self,
        cfg: Config,
        source: Source,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cfg = cfg
        self.source = source
        #: The only wall clock the program reads. App samples it once per loop turn into
        #: ``view.now``; the fetch thread stamps ``Frame.fetched_at`` from it.
        self._clock = clock
        #: Schedules the fetch period; immune to wall-clock steps.
        self._monotonic = monotonic
        self.view = ViewState(cfg)
        self.controller = Controller(self.view, self)
        self._logger = logging.getLogger(_LOGGER_NAME)

        self._initial_enricher = self._build_enricher()

        # --- automatic registry refresh --------------------------------------------
        #: Set while a background download/rebuild is running, so the status line can say
        #: so and a second refresh cannot start on top of the first.
        self._registry_status: str | None = None
        self._registry_thread: threading.Thread | None = None
        self._registry_done = threading.Event()

        self.renderer = Renderer(lambda: self.cfg.display)

        # --- watchlist, alert dispatch, sighting history ---------------------------
        watch_entries: list[watchlist_mod.WatchEntry] = []
        if cfg.watchlist.path:
            try:
                watch_entries = watchlist_mod.load(cfg.watchlist.path)
            except OSError as exc:
                self._logger.warning("could not load watchlist %s: %s", cfg.watchlist.path, exc)
        # The bell is the one channel that must happen on the main thread (curses.beep()
        # is a curses call), so its bell_fn only sets a flag the event loop turns into a beep.
        # Slow channels (webhook, command) run on the worker, never on the fetch thread.
        self._bell_pending = threading.Event()
        self._dispatcher = self._build_dispatcher()
        self._dispatch_worker = dispatch.DispatchWorker(lambda ev: self._dispatcher.maybe_fire(ev))
        self._pipeline = pipeline.Pipeline(
            cfg,
            enricher=self._initial_enricher,
            watch_entries=watch_entries,
            notify=self._dispatch_worker.submit,
            logger=self._logger,
        )

        self._frame_seq = 0

    def _build_dispatcher(self) -> dispatch.Dispatcher:
        return dispatch.Dispatcher(
            self.cfg.alerts, bell_fn=lambda _event: self._bell_pending.set(), now_fn=self._clock
        )

    def _build_enricher(self) -> enrich.Enricher:
        """Assemble the registration/type/owner lookup chain.

        Ordered cheapest-and-most-authoritative first: whatever the receiver itself sent,
        then the downloaded registry database, then purely derived facts (an N-number and a
        country computed from the hex alone). Every provider is optional -- with no
        database at all this still yields a registration and country for US aircraft, which
        is why it is built unconditionally.
        """
        providers: list[enrich.Provider] = [enrich.FeedProvider()]
        db_path = os.path.expanduser(self.cfg.registry.db or "")
        if db_path and os.path.exists(db_path):
            try:
                providers.append(enrich.SqliteProvider(db_path))
            except sqlite3.Error as exc:
                self._logger.warning("registry database unusable (%s): %s", db_path, exc)
        providers.append(enrich.DerivedProvider())
        return enrich.Enricher(providers)

    # ------------------------------------------------------------------
    # Automatic registry refresh
    # ------------------------------------------------------------------

    def _registry_needs_refresh(self) -> bool:
        """True when the registry database is missing or older than registry.max_age_days.

        The rule itself lives in output.registry_is_due so the TUI and the headless service
        cannot drift apart on what "stale" means. "Missing" is the important half: a fresh
        install has no database at all, and requiring someone to find a screen and press a
        key before owner names work is the kind of manual step that leaves the feature
        switched off forever.
        """
        return output.registry_is_due(self.cfg)

    def _start_registry_refresh(self) -> None:
        """Download and rebuild the registry on a background thread, if one is due.

        Runs off the UI thread for the same reason the feed does: this is an 8 MB download
        plus a few hundred thousand SQLite inserts, and none of it may block drawing. The
        enricher is rebuilt on the main thread once the flag is set, since swapping it
        underneath a running fetch would be a data race.
        """
        if self._registry_thread is not None and self._registry_thread.is_alive():
            return
        if not self._registry_needs_refresh():
            return

        db_path = os.path.expanduser(self.cfg.registry.db)

        def _progress(fraction: float, message: str) -> None:
            # registry_update calls this as (fraction, message); the percentage is what
            # belongs in a one-line status, the message is already in the log.
            self._registry_status = f"registry {min(max(fraction, 0.0), 1.0) * 100:.0f}%"

        def _run() -> None:
            self._registry_status = "registry updating"
            try:
                result = registry_update.build_from_tar1090(
                    self.cfg.registry.tar1090_url, db_path, progress_fn=_progress
                )
            except Exception as exc:  # never let a download kill the session
                self._logger.warning("registry update failed: %s", exc)
                self._registry_status = "registry update failed"
            else:
                if result.not_modified:
                    self._logger.info("registry already current")
                else:
                    self._logger.info("registry updated: %d rows", result.row_count)
                self._registry_status = None
                self._registry_done.set()

        self._registry_thread = threading.Thread(target=_run, daemon=True)
        self._registry_thread.start()

    # ------------------------------------------------------------------
    # Background fetch thread -- NEVER call any curses function from here.
    # ------------------------------------------------------------------

    def _fetch_loop(self, stop_event: threading.Event, out_queue: queue.Queue) -> None:
        backoff = BACKOFF_BASE_S
        while not stop_event.is_set():
            due = self._monotonic() + self.cfg.source.refresh_s  # period, not fetch + refresh
            try:
                raw = self.source.fetch(self.cfg.source.timeout_s)
                now = self._clock()
                result = self._pipeline.tick(raw, now, self.view.search_text)
                display = self.cfg.display
                ordered = sort_aircraft(result.aircraft, display.sort_key, display.sort_reverse)
                frame = self._frame(now, result.snapshot, ordered, None, result.feed_stale)
                self._publish(out_queue, frame)
                backoff = BACKOFF_BASE_S
                stop_event.wait(max(0.0, due - self._monotonic()))
            except Exception as exc:  # SourceError is expected; anything else is a bug, banner it
                if isinstance(exc, SourceError):
                    self._logger.warning("fetch failed: %s", exc)
                else:
                    self._logger.exception("unexpected error in fetch loop: %s", exc)
                self._publish(out_queue, self._frame(self._clock(), None, [], str(exc), False))
                stop_event.wait(backoff)
                backoff = min(backoff * 2, self.cfg.source.backoff_max_s)

    def _frame(
        self,
        fetched_at: float,
        snapshot: Any,
        aircraft: list[Aircraft],
        error: str | None,
        feed_stale: bool,
    ) -> Frame:
        self._frame_seq += 1
        return Frame(self._frame_seq, fetched_at, snapshot, aircraft, error, feed_stale)

    @staticmethod
    def _publish(out_queue: queue.Queue, item: Frame) -> None:
        """Drop-oldest publish: clear the queue first so the UI thread only ever sees the
        latest tick and never blocks a slow consumer, then put the new item."""
        while True:
            try:
                out_queue.get_nowait()
            except queue.Empty:
                break
        # pragma: no cover branch below - Full can't happen right after draining above.
        with contextlib.suppress(queue.Full):
            out_queue.put_nowait(item)

    def _layout(self, stdscr) -> Layout:
        height, width = stdscr.getmaxyx()
        return layout_for(width, height, self.view)

    def _paint(self, stdscr) -> None:
        """Lay out, keep the cursor in view, and draw ``self.view``."""
        view = self.view
        view.registry_note = self._registry_status
        layout = self._layout(stdscr)
        if not layout.too_small:
            view.clamp(len(view.rows), layout.table_height)
        self.renderer.draw(stdscr, view, layout)

    # --- Effects: what the Controller asks of the rest of the program ----------------

    @property
    def watch_entries(self) -> list[watchlist_mod.WatchEntry]:
        return self._pipeline.watch_entries

    def refresh_now(self) -> None:
        """Nothing waits on the fetch period yet; B2-8 wakes the fetch loop here."""

    def set_enricher(self) -> None:
        self._pipeline.enricher = self._build_enricher()

    def set_dispatcher(self) -> None:
        self._dispatcher = self._build_dispatcher()

    def set_watch_entries(self, entries: list[watchlist_mod.WatchEntry]) -> None:
        self._pipeline.watch_entries = entries

    def set_pipeline_cfg(self, cfg: Config) -> None:
        self.cfg = cfg
        self._pipeline.cfg = cfg

    def save_config(self, cfg: Config, edited: set[tuple[str, str]]) -> str | None:
        path = cfg._config_path or default_config_path()
        layer = layer_for_save(cfg._file_layer, cfg, edited)
        try:
            config_write.dump_layer(layer, path)
        except OSError as exc:
            self._logger.warning("could not save configuration to %s: %s", path, exc)
            return f"could not save settings: {exc.strerror or exc}"
        cfg._config_path = path
        cfg._file_layer = layer
        self._logger.info("configuration saved to %s", path)
        return None

    # ------------------------------------------------------------------
    # Main event loop
    # ------------------------------------------------------------------

    def _event_loop(self, stdscr, stop_event: threading.Event, out_queue: queue.Queue) -> int:
        view = self.view

        while True:
            view.tick(self._clock())
            while True:
                try:
                    view.apply(out_queue.get_nowait())
                except queue.Empty:
                    break

            if view.dirty:
                self._paint(stdscr)
                view.dirty = False

            # A finished registry download changes what the enricher can resolve, so it
            # is swapped in here, on the main thread, rather than under a running fetch.
            if self._registry_done.is_set():
                self._registry_done.clear()
                self.set_enricher()
                view.dirty = True

            # The bell fires here rather than on the fetch thread: curses.beep() is a
            # curses call, and only this thread may make one.
            if self._bell_pending.is_set():
                self._bell_pending.clear()
                if self.cfg.alerts.bell:
                    with contextlib.suppress(curses.error):
                        curses.beep()

            key = stdscr.getch()
            if key == -1:
                continue
            view.dirty = True

            if key == curses.KEY_RESIZE:
                continue

            if self.controller.handle_key(key, self._layout(stdscr)) is Outcome.QUIT:
                return 0

    def run(self, stdscr) -> int:
        """Entry point for curses.wrapper(app.run). Returns the process exit code."""
        stop_event = threading.Event()
        out_queue: queue.Queue = queue.Queue(maxsize=2)
        fetch_thread = threading.Thread(
            target=self._fetch_loop, args=(stop_event, out_queue), daemon=True
        )
        try:
            # Some terminals do not support cursor visibility control at all -- this must
            # never crash startup.
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            stdscr.timeout(GETCH_TIMEOUT_MS)
            self.renderer.setup()

            fetch_thread.start()
            self._start_registry_refresh()
            return self._event_loop(stdscr, stop_event, out_queue)
        except KeyboardInterrupt:
            return 0
        finally:
            stop_event.set()
            fetch_thread.join(timeout=2.0)
            self._dispatch_worker.close(timeout=0.5)
