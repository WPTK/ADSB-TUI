"""First-run setup: put the wizard in front of a user who has no usable config yet.

Without this, a fresh install fails with "source.url is required" and a pointer to the
README -- which is correct, but it is also the exact moment the setup wizard exists to
handle, so leaving it unreachable made the wizard decorative.

The wizard only ever runs for a person at a terminal. A cron job, a systemd unit, a Docker
healthcheck or anything piping our output must keep getting the clear error message and a
non-zero exit code rather than an interactive UI it cannot answer -- so a non-interactive
run, or any of the scripted modes, is deliberately left alone.
"""

from __future__ import annotations

import contextlib
import curses
import logging
import os
import sys

from . import config_write
from .config import Config, ConfigError, find_config_path, validate
from .ui.screens.setup import SetupScreen

log = logging.getLogger("adsbtui")

#: Where a config written by the wizard goes when the user has no config file at all.
DEFAULT_CONFIG_PATH = "~/.config/adsbtui/config.toml"

#: Flags that mean "this run is scripted": the caller wants output or an exit code, not a
#: wizard, even on a terminal.
_SCRIPTED_FLAGS = ("once", "watch", "headless", "batch", "check")


def should_offer_setup(args, *, stdin=None, stdout=None) -> bool:
    """Decide whether a failed config load should become the setup wizard.

    Requires all of: an interactive terminal on both ends (the wizard has to draw and to
    read keys), no scripted mode requested, and no explicit --config, since naming a file
    that then fails to load is a mistake to report rather than a blank slate to fill in.
    """
    if args is None:
        return False
    if getattr(args, "config", None):
        return False
    if any(getattr(args, flag, None) for flag in _SCRIPTED_FLAGS):
        return False

    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    try:
        return bool(stdin.isatty() and stdout.isatty())
    except (AttributeError, ValueError):
        # A closed or replaced stream: assume not interactive, which is the safe default.
        return False


def _drive(stdscr, screen: SetupScreen) -> bool:
    """Run one screen to completion on a curses window. True if the user finished it."""
    # The wizard is a form, so a visible cursor is the point; not every terminal can.
    with contextlib.suppress(curses.error):
        curses.curs_set(1)
    stdscr.keypad(True)

    while True:
        height, width = stdscr.getmaxyx()
        stdscr.erase()

        box_width = max(20, min(width - 2, 78))
        lines = screen.render_lines(box_width, max(1, height - 2))
        title = f" {screen.title} "[: max(0, box_width)]

        with contextlib.suppress(curses.error):
            stdscr.addstr(0, 0, title[: max(0, width - 1)], curses.A_BOLD)
        for index, line in enumerate(lines):
            row = index + 2
            if row >= height:
                break
            with contextlib.suppress(curses.error):
                stdscr.addstr(row, 0, line[: max(0, width - 1)])
        stdscr.noutrefresh()
        curses.doupdate()

        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        if screen.handle_key(key) == "close":
            return bool(getattr(screen, "applied", False))


def run_setup(screen: SetupScreen | None = None) -> Config | None:
    """Run the wizard in curses. Returns the finished Config, or None if it was cancelled.

    Only ever called once should_offer_setup() has agreed the run is interactive.
    """
    screen = SetupScreen() if screen is None else screen
    try:
        finished = curses.wrapper(_drive, screen)
    except curses.error as exc:
        log.warning("could not start the setup wizard: %s", exc)
        return None
    except KeyboardInterrupt:
        return None
    return screen.result() if finished else None


def save_new_config(cfg: Config, path: str | None = None) -> str | None:
    """Persist a wizard-produced config. Returns the path written, or None on failure."""
    target = os.path.expanduser(path or find_config_path(None) or DEFAULT_CONFIG_PATH)
    try:
        config_write.dump_config(cfg, target)
    except OSError as exc:
        log.warning("could not write configuration to %s: %s", target, exc)
        return None
    return target


def first_run(
    args, *, setup_fn=None, save_fn=None, stdin=None, stdout=None
) -> tuple[Config, str | None] | None:
    """The whole first-run flow: offer the wizard, validate what it produced, save it.

    Returns (config, written path) when the user completed setup, or None to let the
    caller report the original configuration error and exit. A config the wizard somehow
    produced that still does not validate is treated as a cancellation rather than being
    forced on the session.
    """
    if not should_offer_setup(args, stdin=stdin, stdout=stdout):
        return None

    # Injectable so tests can drive the whole flow without a terminal; production uses the
    # real curses wizard and the real writer.
    setup_fn = run_setup if setup_fn is None else setup_fn
    save_fn = save_new_config if save_fn is None else save_fn

    print("No configuration found. Starting setup -- press Esc at any point to cancel.")
    cfg = setup_fn()
    if cfg is None:
        return None

    try:
        validate(cfg)
    except ConfigError as exc:
        print(f"Setup produced an unusable configuration: {exc}", file=sys.stderr)
        return None

    written = save_fn(cfg)
    if written:
        print(f"Configuration saved to {written}")
        cfg._config_path = written
    else:
        print("Continuing with these settings, but they could not be saved.", file=sys.stderr)
    return cfg, written
