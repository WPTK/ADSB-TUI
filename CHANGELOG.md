# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **The six in-app screens are reachable**: help (`F1`/`?`), aircraft detail (`Enter`), sort
  (`s`), filters and search (`f`), column chooser (`c`), and watchlist editor (`w`). `Esc`
  cancels, `Enter` applies, and watchlist edits are saved back to disk.
- **The table scrolls** with the arrow keys, `j`/`k`, PageUp/PageDown, and Home/End, with the
  selected row highlighted (F27).
- **Alerts are delivered, not just graded**: a level transition or a watchlist match fires the
  configured channels (terminal bell, desktop notification, webhook, shell command) with
  per-aircraft cooldowns and quiet hours. Delivery runs on the fetch thread so a slow webhook
  cannot stall the display; the bell is handed back to the UI thread, since it is a curses call.
- **Sighting history**: each track is summarised (closest approach, highest altitude, fastest
  speed, squawks seen, whether it declared an emergency) and written to SQLite when the track
  ends, with retention pruning at startup.
- **The remaining filters apply**: `hide_ground`, `include_nonicao`, and the altitude band now
  affect what you see, alongside a live text search across callsign, hex, registration, owner,
  and type.
- `src/adsbtui/__main__.py`: the console entry point. `adsbtui` launches the curses TUI;
  `--check`, `--once`, `--watch N`, `--batch`, and `--headless` select the non-interactive
  modes. Exit codes: 0 clean, 2 configuration/terminal error, 4 source unreachable.
- `tests/test_tui_smoke.py`: end-to-end tests that launch the real UI inside a pseudo-terminal
  and assert a clean exit on `q`, on `q` after pausing, and on Ctrl-C, at 120x30, 80x24, and
  40x10 (F04, F25, F26).
- `AUDIT.md`: full audit findings table with status tracking (F01-F47).
- `tests/fixtures/sample_master.csv`: 50-row FAA registry sample for tests and docs.
- `contrib/adsb-tui-kiosk.service` and `contrib/tmux.md`.
- `.gitignore` for bytecode caches, logs, local config, and the local registry copy.

### Changed
- **Rewritten as a package.** The single 181-line script is now `src/adsbtui/` with no
  third-party dependencies at all (the old version required `pandas` and `requests`), a TOML
  config file with CLI-flag and environment-variable overrides, and 634 tests.
- **Breaking: Python 3.11+ is required** (for `tomllib`).
- **Breaking: displayed speeds change.** Ground speed was being converted with a
  kilometres-to-miles factor although the feed reports knots, so every speed shown was ~46% low.
  A 460-knot airliner now correctly reads 529 mph instead of 286 (F01).
- **Breaking: configuration moved out of the source file** into `~/.config/adsbtui/config.toml`
  (or `--config`/`ADSBTUI_*`). Editing module constants no longer does anything (F09).
- `adsbtui.py` at the repository root is now a thin compatibility shim that forwards to the
  package, so `python3 adsbtui.py ...` keeps working.
- `MASTER.csv` removed from version control (F30); supply your own registry CSV via
  `registry.path`.
- README rewritten (F12-F15, F45): the old one documented a script name that never existed, a
  nonexistent `curses` PyPI package, a placeholder clone URL, the wrong FAA download URL and
  delimiter, and `chmod`/`chown` steps that do nothing.

### Fixed
- One malformed aircraft record no longer discards the whole fetch and blanks the table (F08).
- The UI no longer blocks on the network: fetching runs on a background thread, so the quit key
  and resize stay responsive during a slow or dead feed (F03).
- Unguarded `addstr` calls no longer crash the program on a narrow or short terminal; the table
  now drops low-priority columns as it narrows and shows a "terminal too small" panel below a
  usable size (F04, F25).
- Failures are visible on screen instead of silently logged: the status line reports the source
  state, the last error, and the age of the last successful fetch (F05).
- Logging is rotated, scoped to the `adsbtui` logger, and no longer opens a DEBUG file in the
  working directory at import time (F06).
- The display repaints only when something changed, instead of clearing the whole screen ten
  times a second (F07).
- `alt_baro: "ground"` renders as GND, and a genuine 0 ft or 0 kt no longer renders as "N/A"
  (F23).
- Non-ICAO `~`-prefixed addresses are no longer treated as ICAO hex codes (F22).
- Stale positions are aged out using `seen_pos` rather than being shown as current (F17).
- Alerts are graded from predicted closest point of approach, emergency squawks, and the
  reported emergency state, not just current distance (F19, F21).
- Bearing from home is shown alongside the aircraft's own ground track, which the original
  mislabelled as "heading" (F24).
- The receiver's own database enrichment (registration, type, description, operator, military/
  PIA/LADD flags) is read from the feed instead of being discarded (F20, F31).
- Compass sectors no longer mis-round at exact boundaries, and the unreachable 17th entry is
  gone (F40).
- Untrusted strings from the network are sanitized before reaching curses, so an embedded NUL
  or control character can no longer crash or corrupt the display (F33).
- Responses are size-capped, so a broken or hostile feed cannot exhaust memory (F34).
- Ctrl-C exits cleanly instead of dumping a traceback through a half-restored screen (F26).
