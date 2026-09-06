"""Console entry point: assemble config, data source, and a run mode.

This module is deliberately thin. It owns exactly three decisions -- which config to
build, which source to open, and which run mode to hand them to -- and delegates
everything else. Keeping it that small is what lets every other module stay independently
testable: nothing here does I/O of its own beyond printing a startup error.

Run modes, in the order they are checked:

  --check              one fetch, print a one-line verdict, exit (for healthchecks)
  --once               one fetch, print the table/json/csv, exit
  --watch N            N fetch-and-print cycles, then exit
  --batch              a line-per-event stream on stdout, for piping
  --headless           fetch/log loop with no output, for running as a service
  (default)            the curses TUI
"""

from __future__ import annotations

import contextlib
import curses
import logging
import signal
import sys
import threading

from . import __version__
from .config import ConfigError, load_config
from .logsetup import configure_logging
from .output import run_batch, run_headless, run_once, run_watch
from .sources import SourceError, make_source
from .ui.app import App, run_check

log = logging.getLogger("adsbtui")


def _stop_predicate_from_signals() -> threading.Event:
    """Return an Event set by SIGINT/SIGTERM, so headless mode exits cleanly on Ctrl-C
    or `systemctl stop` instead of dying with a traceback mid-fetch."""
    stop = threading.Event()

    def _handler(signum, frame):  # noqa: ARG001 - signal handler signature is fixed
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        # Not on the main thread, or the platform lacks the signal: the loop still works,
        # it just will not be interruptible this way.
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, _handler)
    return stop


def main(argv: list[str] | None = None) -> int:
    """Entry point for the `adsbtui` console script. Returns a process exit code.

    Exit codes: 0 success/clean quit, 2 configuration error, 4 the source was
    unreachable or returned unusable data.
    """
    try:
        cfg = load_config(argv)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        print(
            "Run 'adsbtui --help', or see the README's Configuration section.",
            file=sys.stderr,
        )
        return 2

    configure_logging(
        log_file=cfg.logging.file,
        level="DEBUG" if getattr(cfg._args, "debug", False) else cfg.logging.level,
        max_bytes=cfg.logging.max_bytes,
        backup_count=cfg.logging.backup_count,
    )
    log.info("adsbtui %s starting (config: %s)", __version__, cfg._config_path or "defaults")

    try:
        source = make_source(cfg.source.url, max_bytes=cfg.source.max_bytes)
    except (SourceError, ValueError) as exc:
        print(f"Cannot use source {cfg.source.url!r}: {exc}", file=sys.stderr)
        return 2

    args = cfg._args
    fmt = getattr(args, "format", None) or "table"

    if getattr(args, "check", False):
        return run_check(cfg, source)

    if getattr(args, "once", False):
        return run_once(cfg, source, fmt)

    watch_n = getattr(args, "watch", None)
    if watch_n is not None:
        return run_watch(cfg, source, fmt, cfg.source.refresh_s, iterations=watch_n)

    if getattr(args, "batch", False):
        stop = _stop_predicate_from_signals()
        try:
            for line in run_batch(cfg, source, diff_only=False):
                print(line, flush=True)
                if stop.is_set():
                    break
        except KeyboardInterrupt:
            pass
        return 0

    if getattr(args, "headless", False):
        stop = _stop_predicate_from_signals()
        return run_headless(cfg, source, stop_predicate=stop.is_set)

    # Default: the curses TUI. curses.wrapper restores the terminal even if run() raises.
    app = App(cfg, source)
    try:
        return curses.wrapper(app.run)
    except KeyboardInterrupt:
        return 0
    except curses.error as exc:
        print(f"Terminal error: {exc}", file=sys.stderr)
        print("If this terminal cannot run curses, try 'adsbtui --once' instead.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
