# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **A radar scope.** Aircraft are plotted around you with range rings, N/E/S/W ticks and a
  direction arrow per contact, so the shape of the traffic is visible instead of implied by a
  column of numbers. Appears automatically at 110 columns and wider; `r` toggles it, and it
  degrades to ASCII where the border style asks for it.
- **Rows are colored by altitude band** when nothing more urgent applies: warm low, cool high.
  An ordinary screenful now carries information rather than being one flat color.
- **A bare receiver address works as a source.** `--url 192.168.3.80` (or a hostname, or
  `host:8080`) probes the paths receiver images actually publish aircraft.json at and keeps
  whichever answers. It previously fell through to the file loader and failed with "file not
  found: 192.168.3.80", having never touched the network at all.
- **The registry downloads itself** in the background when it is missing or older than
  `registry.max_age_days` (now 1 day, was 30). Owner and type data no longer wait for anyone to
  find a screen and press a key; the status line carries the progress.
- **The help screen explains the columns**, not just the keys.
- **The table sorts by any visible field**, not just distance, altitude, or callsign:
  registration, type, ground speed, vertical speed, bearing, closest-point-of-approach,
  owner, MIL/PIA/LADD/category flags, position age, alert level, and the raw hex code are
  all now options on the Sort screen (`s`/`F5`). Sorting by CPA and by flags matches what
  the column itself shows (an on-ground aircraft's hidden CPA projection stays hidden from
  the sort too, and flags sort by the same decoded tag text the column renders); sorting
  by alert level ranks by severity, least alarming to `EMERGENCY`.
- **A first run with no config starts the setup wizard** instead of failing with
  "source.url is required" and a pointer to the README -- which was the exact moment the
  wizard existed to handle. It only ever runs for a person at a terminal: scripted modes
  (`--once`, `--watch`, `--headless`, `--batch`, `--check`), a piped or redirected run,
  and an explicit `--config` that fails to load all keep the plain error and exit code 2,
  since a cron job or systemd unit cannot answer a UI.
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
- **Owner and type lookup chains multiple sources**: readsb's own feed enrichment (`r`/`t`/
  `desc`/`ownOp`/`dbFlags`) wins first, then the downloaded SQLite registry, then a US N-number
  and country derived from the hex code alone -- each stage only filling in what the previous
  one left blank.
- **A Data screen** (`F8`/`D`) shows each registry source's on-disk state, row count, age and
  license, and forces a rebuild on a background thread with a progress bar. Routine refreshes
  now happen on their own; this screen is for seeing what you have (F30, F31).
- **A Settings screen** (`F2`/`,`) covers every config section: current value, default, and a
  one-line explanation per field, inline validation, and a Save that writes `config.toml` and
  applies the change to the running session immediately (F09, F16).
- **`--headless` is a real background service**, not just a fetch-and-log loop: it now runs the
  same tracking, watchlist matching, filtering, alert-grading, alert-dispatch, and history
  pipeline the curses UI does, so a systemd unit actually delivers the bell/desktop/webhook/
  command notifications it advertises instead of only logging one line per cycle.
- `src/adsbtui/__main__.py`: the console entry point. `adsbtui` launches the curses TUI;
  `--check`, `--once`, `--watch N`, `--batch`, and `--headless` select the non-interactive
  modes. Exit codes: 0 clean, 2 configuration/terminal error, 4 source unreachable.
- `tests/test_tui_smoke.py`: end-to-end tests that launch the real UI inside a pseudo-terminal
  and assert a clean exit on `q`, on `q` after pausing, and on Ctrl-C, at 120x30, 80x24, and
  40x10 (F04, F25, F26).
- `AUDIT.md`: full audit findings table with status tracking (F01-F47).
- `contrib/adsb-tui-kiosk.service` and `contrib/tmux.md`.
- `.gitignore` for bytecode caches, logs, local config, and the local registry copy.

### Changed
- **The table header is in plain English.** `GS`/`VS`/`BRG`/`CPA` are now
  `SPEED`/`CLIMB`/`DIRECTION`/`CLOSEST PASS`, with short forms kept only for terminals too
  narrow for the real words.
- **Columns are separated by whitespace, not box-drawing pipes.** A grid of pipes reads as a
  cramped mess; every table tool people actually like separates with space.
- **The screen furniture is rearranged.** The title bar is gone entirely: it spent a row on the
  program name, its version, the current unit system and the config file path, none of which
  change while you watch. The top row is now live state only (link health, counts, message rate,
  clock), and the key hints moved to the bottom, out of the way of the table.
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
- `MASTER.csv` removed from version control (F30); the registry is downloaded and built
  automatically instead.
- README rewritten (F12-F15, F45): the old one documented a script name that never existed, a
  nonexistent `curses` PyPI package, a placeholder clone URL, the wrong FAA download URL and
  delimiter, and `chmod`/`chown` steps that do nothing.
- **README rewritten again, end to end**: cut by nearly half, reformatted into scannable tables
  instead of prose paragraphs, and dropped a "Contributing" section that made no sense for a
  single-maintainer project.

### Removed
- **The legacy FAA registry CSV, completely**: `registry.path`, the `--registry` flag, the CSV
  enrichment provider, the loader, and the bundled sample fixture. The downloaded database and
  the receiver's own feed cover everything it did, and keeping a second, manual, US-only path
  around left the docs describing a workflow nobody should follow.

### Fixed
- `--once`/`--watch`/`--batch` previously ignored `registry.path` and every `[filter]` setting
  beyond radius, and never computed elevation angle -- so OWNER was always N/A outside the TUI
  and JSON/CSV output disagreed with what the curses screen showed for the same feed.
- The derived US N-number (used as a last-resort fallback when no registry has a match) was
  wrong for most aircraft; corrected against the FAA's actual suffix allocation scheme.
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
