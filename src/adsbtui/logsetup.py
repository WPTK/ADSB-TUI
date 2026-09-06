"""Logging setup for the "adsbtui" logger.

Deliberately configures only the "adsbtui" named logger -- never the bare root logger via
logging.basicConfig(). The original tool's bug was calling basicConfig() on the root logger,
which meant every third-party library's DEBUG-level chatter (urllib3, http.client, etc.) got
captured right along with adsbtui's own messages the moment the user turned on debug logging.

Also deliberately never attaches a StreamHandler: a curses session owns the terminal's stdout
and stderr completely, and any stray write to either from a background thread would corrupt
the screen. If log_file is falsy, configure_logging() does nothing at all -- no handlers,
meaning log records are silently dropped (as they would be by logging's own "handler of last
resort" warning-once behavior, minus even that one warning line to the terminal).
"""

from __future__ import annotations

import logging
import logging.handlers
import os

LOGGER_NAME = "adsbtui"


def configure_logging(
    log_file: str,
    level: str,
    max_bytes: int,
    backup_count: int,
) -> None:
    """Configure the "adsbtui" logger with a rotating file handler.

    If `log_file` is falsy (empty string, None), no handler is attached at all -- logging
    calls against the "adsbtui" logger are then simply no-ops, and in particular nothing is
    ever written to stdout/stderr (which would corrupt a running curses screen).

    Otherwise, `log_file` is expanduser()'d, its parent directory is created (os.makedirs
    with exist_ok=True) if needed, and a logging.handlers.RotatingFileHandler is attached at
    that path with the given max_bytes/backup_count. The logger's level is set from `level`
    (a case-insensitive name such as "DEBUG", "INFO", "WARNING", "ERROR").

    Defensively forces the "urllib3" logger to WARNING regardless of the level requested here,
    in case that library is ever present in the dependency graph -- and, more generally,
    nothing in this module ever raises logging.getLogger("adsbtui").setLevel(logging.DEBUG)
    style calls against http.client, whose debug output writes straight to stdout via print()
    and would corrupt curses just as badly as a stray log handler would.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()

    log_level = getattr(logging, level.upper())
    logger.setLevel(log_level)

    if log_file:
        path = os.path.expanduser(log_file)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=max_bytes, backupCount=backup_count
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)

    # Defensive: keep urllib3 (and any future dependency that logs through it) quiet even if
    # the "adsbtui" logger itself is set to DEBUG. http.client's own debug output does not go
    # through logging at all (it uses print()), so the only defense against that is never
    # setting http.client.HTTPConnection.debuglevel, which this module simply never touches.
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger.propagate = False
