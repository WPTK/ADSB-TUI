# ADSB-TUI

ADSB-TUI is a zero-dependency, Python 3.11+ terminal UI for tracking nearby aircraft from a
`readsb`/`dump1090-fa`-compatible ADS-B feed. It polls your receiver's `aircraft.json` in the
background, converts every unit correctly, works out each aircraft's distance and bearing from
your location, projects a closest-point-of-approach for aircraft heading your way, and colors the
table by how alarmed you should be — all without ever blocking the display on the network.

> **Status:** this is a ground-up rewrite of the original 181-line `adsbtui.py` script into a
> tested package (see `AUDIT.md`/`CHANGELOG.md` for why). The config loader, data pipeline, curses
> table, alert grading, and the `--once`/`--watch`/`--headless`/`--batch` renderers documented
> below are all implemented and covered by `tests/`. The thin script that assembles them into one
> runnable `adsbtui` command (`src/adsbtui/__main__.py`) has **not landed yet** in this snapshot —
> the console script `pip install` creates will currently fail with `ModuleNotFoundError` when
> run. See [Known limitations](#known-limitations) for the full, honest list of what is and isn't
> wired up yet.

---

## Features

- **Background fetch thread**: the curses UI never blocks on the network; a daemon thread polls
  on `source.refresh_s` with exponential backoff on failure, and the main thread only drains a
  queue and redraws when something actually changed.
- **Correct units**: ground speed is stored internally in knots and converted properly to mph/
  km/h/kt for display — the original tool's bug silently understated speed by ~46%.
- **Resize-safe, crash-resistant table**: every draw call is guarded against `curses.error`, the
  UI degrades to a "Terminal too small" message below a sane minimum size instead of crashing, and
  a configurable, terminal-width-aware set of columns (flight, registration, type, altitude,
  vertical-speed trend, ground speed, distance, bearing, closest-point-of-approach, owner, MIL/
  PIA/LADD/category flags, position age, alert) drops least-useful columns first as the terminal
  narrows.
- **Alert grading**: every aircraft is graded `NONE`/`OUTBOUND`/`INBOUND`/`OVERHEAD`/`EMERGENCY`
  from a priority-ordered set of checks — a watched emergency squawk or reported emergency state,
  currently inside your proximity radius, projected to close within a configurable
  closest-point-of-approach distance and time horizon, or recently close and now receding. The
  grade drives the TUI's row coloring and is included as `alert_level` in every non-interactive
  output format.
- **Owner lookup**: an optional local FAA-format registry CSV maps a hex code straight to an
  owner name (see [Owner lookup](#owner-lookup)); readsb/dump1090's own `--db-file` enrichment
  fields (registration, type, description, operator, dbFlags) are read from the feed directly with
  no extra download at all.
- **Non-interactive modes**: `--once`, `--watch N`, `--headless`, and `--batch`, each with
  `table`/`json`/`csv` output where applicable, plus `--check` for a scripted health check — for
  cron jobs, systemd services, and shell pipelines. This code path has zero curses dependency.
- **Live controls**: pause the display, or grow/shrink the filter radius, without restarting.
- **A larger set of screens exists in the codebase** — a help overlay, a read-only aircraft detail
  pane, and editor screens for sort order, filters, visible columns, and the watchlist — each
  fully implemented as a pure, unit-tested "Screen" component. They are not yet reachable by
  keystroke from the running app; see [Known limitations](#known-limitations).

---

## Installation

Requires **Python 3.11+**. No third-party dependencies on Linux/macOS.

```bash
git clone https://github.com/WPTK/ADSB-TUI.git
cd ADSB-TUI
pip install -e .
```

Or with [pipx](https://pypa.github.io/pipx/) once you're not actively developing on it:

```bash
pipx install git+https://github.com/WPTK/ADSB-TUI.git
```

On Windows, curses isn't in the standard library — install the optional extra:

```bash
pip install -e ".[windows]"
```

To run the test suite / linter, install the `dev` extra instead: `pip install -e ".[dev]"`.

---

## Quick start

Create a config file at `~/.config/adsbtui/config.toml` (or `./adsb-tui.toml` in your working
directory, or point `--config`/`ADSBTUI_CONFIG` at any path):

```toml
[source]
url = "http://192.168.1.50/skyaware/data/aircraft.json"

[home]
lat = 37.7749
lon = -122.4194
```

Then run:

```bash
adsbtui
```

The equivalent invocation with no config file at all, using CLI flags only:

```bash
adsbtui --url http://192.168.1.50/skyaware/data/aircraft.json --lat 37.7749 --lon -122.4194
```

`home.lat`/`home.lon` are required — `(0.0, 0.0)` is treated as the unedited placeholder and
rejected — and `source.url` is required and must not be empty.

---

## Configuration reference

Precedence is **CLI flag > `ADSBTUI_*` environment variable > TOML file > the default below**.
Every key is parsed, type-coerced, and validated by `config.py` regardless of whether the running
app currently *reads* it — rows below marked "not yet wired" are stored on the `Config` object and
covered by tests, but nothing in the fetch loop, curses UI, or CLI runners consumes them yet in
this snapshot; see [Known limitations](#known-limitations) for the consolidated picture.

Environment variable names follow `ADSBTUI_<SECTION>_<KEY>`, e.g. `ADSBTUI_HOME_LAT=37.7749`.

### `[source]`

| Key | Default | Meaning |
|---|---|---|
| `url` | `""` (required) | `aircraft.json` source: an `http://`/`https://` URL, a bare filesystem path, or a `file://` URL. |
| `refresh_s` | `5.0` | Seconds between polls. |
| `timeout_s` | `5.0` | Per-fetch timeout, in seconds. |
| `stale_s` | `30.0` | Intended max acceptable age of the feed's own data before the *source* is considered stale. Not yet wired. |
| `backoff_max_s` | `30.0` | Ceiling for the fetch thread's exponential backoff (1s, 2s, 4s, ...) after a failed fetch. |
| `max_bytes` | `8000000` | Reject a response larger than this many bytes. |

### `[home]`

| Key | Default | Meaning |
|---|---|---|
| `lat` | `0.0` (required) | Home latitude, degrees. |
| `lon` | `0.0` (required) | Home longitude, degrees. |

### `[filter]`

| Key | Default | Meaning |
|---|---|---|
| `radius` | `15.0` | Miles; aircraft farther than this are dropped (an emergency aircraft can bypass this — see `alerts.emergency_ignore_radius`). |
| `proximity` | `5.0` | Miles; the `OVERHEAD` alert-grading threshold. |
| `hide_ground` | `false` | Intended to hide aircraft on the ground. Edited by the Filters screen; not yet applied by the fetch pipeline. |
| `include_nonicao` | `true` | Intended to include/exclude `~`-prefixed TIS-B/MLAT addresses. Not yet wired. |
| `min_alt_ft` | `0` | Intended minimum-altitude filter. Not yet wired. |
| `max_alt_ft` | `60000` | Intended maximum-altitude filter. Not yet wired. |

### `[display]`

| Key | Default | Meaning |
|---|---|---|
| `units` | `"imperial"` | `imperial`, `metric`, or `aviation` — controls every displayed speed/altitude/distance unit. |
| `theme` | `"default"` | Reserved for a future selectable color theme; only one theme exists today. |
| `color` | `"auto"` | `auto`/`always`/`never`. `never` disables curses color (same effect as `--no-color`). |
| `columns` | `["flight","reg","type","alt","vs","gs","dist","brg","cpa","owner","flags","age","alert"]` | Which table columns to show, and in what order. Unknown keys are ignored; `flight` is never dropped even on a very narrow terminal. |
| `owner_width` | `30` | Preferred width, in characters, of the owner column. |
| `borders` | `"unicode"` | `unicode`/`ascii`/`none` box-drawing style for the header divider and column separators. |
| `density` | `"normal"` | Reserved for a future row-density mode; not yet read by the renderer. |
| `sort_key` | `"distance"` | `distance`, `altitude`, or `callsign`. Aircraft with no value for the chosen key always sort last. |
| `sort_reverse` | `false` | Reverse the sort order. |
| `stale_after_s` | `15.0` | Seconds since the last position update before a row is marked stale (dimmed). |
| `linger_s` | `30.0` | Seconds an aircraft that dropped out of the feed is still shown (marked lost) before being removed. |
| `status_bar` | `true` | Show/hide the bottom status line. |
| `vs_threshold_fpm` | `256.0` | Intended vertical-speed threshold, ft/min, for the climb/descend/level trend arrow. Not yet threaded through to the renderer, which currently uses an identical hardcoded default. |

### `[alerts]`

| Key | Default | Meaning |
|---|---|---|
| `emergency` | `true` | Grade a watched squawk or a reported emergency state as `EMERGENCY`. Active today. |
| `squawks` | `[7500, 7600, 7700]` | Squawk codes graded as emergencies. Active today. |
| `emergency_ignore_radius` | `true` | Let an emergency aircraft bypass `filter.radius`. Active today. |
| `cpa_enabled` | `true` | Enable the closest-point-of-approach `INBOUND` projection. Active today. |
| `cpa_distance` | `1.0` | Miles; CPA distance threshold for `INBOUND`. Active today. |
| `cpa_horizon_s` | `600.0` | Seconds; how far into the future a CPA solution can be and still count as `INBOUND`. Active today. |
| `cpa_min_gs_kt` | `30.0` | Minimum ground speed, knots, before a CPA projection counts (filters out effectively-parked aircraft). Active today. |
| `events` | `["proximity","cpa","watchlist","emergency"]` | Which event families the alert dispatcher acts on. Consumed by `dispatch.py`, which is implemented and unit-tested but **not yet invoked** by the running app or CLI runners. |
| `bell` | `true` | Ring the terminal bell on a dispatched event. Same not-yet-wired status as `events`. |
| `desktop` | `false` | Send a desktop notification (`notify-send`/`osascript`). Same status. |
| `webhook_url` | `""` | POST a JSON/ntfy/Discord-formatted payload here on a dispatched event. Same status. |
| `webhook_format` | `"auto"` | `auto`/`json`/`ntfy`/`discord`. |
| `command` | `""` | Shell command (word-split, not run through a shell) executed with `HEX`/`REG`/`CALL`/`DIST`/`ALT` in its environment. Same not-yet-wired status. |
| `cooldown_s` | `600.0` | Minimum seconds between repeat dispatches for the same (aircraft, event) pair. |
| `quiet_hours` | `""` | `"HH:MM-HH:MM"` window (may wrap past midnight) during which bell/desktop are suppressed; webhook/command still fire. A malformed value is treated as "no quiet hours". |

### `[watchlist]`

| Key | Default | Meaning |
|---|---|---|
| `path` | `"~/.config/adsbtui/watchlist.txt"` | Plain-text watchlist file (see the file format documented in `src/adsbtui/watchlist.py`). Consumed by the watchlist module and its editor screen; not yet invoked by the fetch loop. |
| `pin_top` | `true` | Intended to pin watchlist matches to the top of the table. Not yet applied. |
| `ignore_radius` | `true` | Intended to let a watchlist match bypass `filter.radius`. Not yet applied. |

### `[registry]`

| Key | Default | Meaning |
|---|---|---|
| `path` | `""` | Path to an FAA-format owner-lookup CSV. If set, it is loaded once at startup and **is** used to fill in the owner column — see [Owner lookup](#owner-lookup). |

### `[history]`

| Key | Default | Meaning |
|---|---|---|
| `db` | `""` | Path to a SQLite database for historical logging. Consumed by `history.py`; not yet invoked anywhere runnable. |
| `retention_days` | `365` | Intended retention window for pruning old history rows. Same status. |

### `[logging]`

| Key | Default | Meaning |
|---|---|---|
| `file` | `"~/.local/state/adsbtui/adsbtui.log"` | Rotating log file path, consumed by `logsetup.configure_logging()`. That function is not yet called from the running app or CLI runners, so no log file is produced by default in this snapshot. |
| `level` | `"INFO"` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `max_bytes` | `1048576` | Rotate after this many bytes. |
| `backup_count` | `3` | Number of rotated backups to keep. |
| `redact_home` | `true` | Intended to redact home lat/lon from log output. Not yet applied. |

### CLI flags

`--config PATH`, `--url`, `--lat`, `--lon`, `--radius`, `--proximity`, `--refresh`, `--registry`,
`--log-file`, `--log-level {DEBUG,INFO,WARNING,ERROR}`, `--units {imperial,metric,aviation}`,
`--no-color`, `--debug` (shortcut for `--log-level DEBUG`) map onto the config keys above. `--once`,
`--watch N`, `--headless`, `--batch`, `--format {table,json,csv}`, and `--check` select a run mode
— see [Non-interactive modes](#non-interactive-modes). Run `adsbtui --help` for the full list once
the entry point is wired up.

---

## Data sources

`adsbtui` reads whatever your receiver's `aircraft.json` looks like — it does not run or configure
`readsb`/`dump1090-fa` itself. Two ways to point `source.url` at it:

**Over HTTP, from another machine on your network.** Most receiver images run a small web server
alongside the decoder. Common paths, depending on your image:

- `http://<receiver-ip>/tar1090/data/aircraft.json` (tar1090)
- `http://<receiver-ip>/skyaware/data/aircraft.json` (FlightAware's SkyAware, dump1090-fa's usual
  lighttpd alias)
- `http://<receiver-ip>:8080/data/aircraft.json` (readsb's/dump1090's own built-in web server,
  default port 8080)

Check which of these actually resolves on your receiver before committing to one — image builders
vary, and none of them serve `aircraft.json` at a raw `/run/readsb/` path over HTTP (that's a
filesystem path readsb writes to locally, not something a web server exposes by default; the
original README's `chmod`/`chown` advice for reaching it over the network was a no-op).

**As a local file, when `adsbtui` runs on the receiver itself.** `readsb` writes `aircraft.json` to
a path like `/run/readsb/aircraft.json` continuously. Point `source.url` straight at it — a bare
path or a `file://` URL both work:

```toml
[source]
url = "/run/readsb/aircraft.json"
# or: url = "file:///run/readsb/aircraft.json"
```

No `chmod`/`chown` dance is needed here either: run `adsbtui` as a user that can already read the
file (e.g. in the same group `readsb` writes it as), and normal filesystem permissions apply like
they would for any other program reading a file on the same box.

---

## Owner lookup

Set `registry.path` to a CSV with (at least) two columns: `MODE S CODE HEX` and `NAME`. Any other
columns are ignored, and the file is read as UTF-8 with BOM tolerance. `tests/fixtures/
sample_master.csv` is a 50-row example of the expected shape:

```csv
N-NUMBER,NAME,MODE S CODE HEX
100,BENE MARY D                                       ,A004B3
10000,9AT LLC                                           ,A00725
```

For the real, full US registry: download **`ReleasableAircraft.zip`** from the FAA's
[Releasable Aircraft Registry download page](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download).
Inside is `MASTER.txt` — a **comma-delimited** file (not pipe-delimited, contrary to the original
README) whose header row already includes `MODE S CODE HEX` and `NAME` among many other columns.
Point `registry.path` straight at `MASTER.txt` (the `.txt` extension doesn't matter; it's parsed
with Python's `csv` module regardless of filename) — no conversion step is required.

This registry only covers **US-registered aircraft**. For everything else, the owner column falls
back to whatever your own receiver's enrichment provides: if `readsb`/`dump1090-fa` is run with a
`--db-file` pointed at their aircraft database, the feed itself carries `r`/`t`/`desc`/`ownOp`/
`dbFlags` (registration, type, description, operator, and military/interesting/PIA/LADD flags) for
non-US and otherwise-unregistered aircraft, and `adsbtui` reads those fields directly with no
extra download of its own — see [Known limitations](#known-limitations) for what still requires
your receiver's own setup versus what `adsbtui` does for you.

---

## Keyboard shortcuts

These are the only bindings currently wired into the running app's event loop:

| Key | Action |
|---|---|
| `q` / `Q` / `F10` | Quit |
| `p` / `P` | Pause / resume the display (the fetch thread keeps running in the background) |
| `+` / `=` | Increase the filter radius by 1 mile |
| `-` / `_` | Decrease the filter radius by 1 mile (minimum 1 mile) |
| *(terminal resize)* | Redraws at the new size automatically |

A help overlay, a read-only aircraft detail pane, and editor screens for sort order, column
selection, filters, and the watchlist all exist as complete, individually unit-tested modules
under `src/adsbtui/ui/screens/` — but none of them is bound to a key yet in `ui/app.py`'s event
loop, so they aren't reachable from a running session in this snapshot. See
[Known limitations](#known-limitations).

---

## Non-interactive modes

All of these avoid curses entirely, so they work in a script, a cron job, or a systemd unit.

```bash
# Fetch once, print a table, and exit (exit code 4 on a fetch error).
adsbtui --once

# Fetch once and print JSON or CSV instead.
adsbtui --once --format json
adsbtui --once --format csv

# Fetch, print, sleep, repeat for 5 cycles, then exit.
adsbtui --watch 5 --format table

# Run forever with no stdout output at all, logging one line per cycle instead
# (a background-service loop; nothing is wired up yet to also write to logging.file).
adsbtui --headless

# Stream SEEN/NEW/LOST lines as aircraft appear and disappear, for scripting/piping.
adsbtui --batch

# Validate config, fetch once, and report OK/ERROR without printing any aircraft data.
adsbtui --check
```

---

## Alerts configuration

`squawks`, `emergency`, and the `cpa_*` keys below already drive the TUI's row coloring and the
`alert_level` field in every output format. The `bell`/`desktop`/`webhook_url`/`command` delivery
channels are fully implemented (with per-event cooldowns and a quiet-hours window) in
`dispatch.Dispatcher`, unit-tested in isolation, but **not yet invoked** by the running app or any
CLI runner — see [Known limitations](#known-limitations).

```toml
[alerts]
emergency = true
squawks = [7500, 7600, 7700]
cpa_enabled = true
cpa_distance = 1.0
cpa_horizon_s = 600
cpa_min_gs_kt = 30

# Reserved for the not-yet-wired dispatcher (see above):
events = ["proximity", "cpa", "emergency"]
bell = true
desktop = true
webhook_url = "https://ntfy.sh/my-adsb-alerts"
webhook_format = "ntfy"
cooldown_s = 600
quiet_hours = "22:00-07:00"
```

---

## Known limitations

Being direct about the state of this snapshot:

- **No top-level entry point yet.** `src/adsbtui/__main__.py` does not exist. `pip install`
  creates the `adsbtui` console script declared in `pyproject.toml`, but running it fails
  immediately with `ModuleNotFoundError` until that wiring lands. Everything else in this document
  describes the config/CLI/data-pipeline surface that script is meant to assemble — it is
  implemented and exercised by `tests/`, just not glued into one runnable command yet.
- **Most of the interactive UI is quit/pause/radius-only.** The help overlay, aircraft detail
  pane, and sort/filter/column-chooser/watchlist-editor screens exist as complete, unit-tested
  components but aren't bound to any key in `ui/app.py`'s event loop yet.
- **Alert *dispatch* (bell/desktop/webhook/shell command) is not yet wired in.** Alert *grading*
  (the color-coded rows and `alert_level` field) works today; actually ringing a bell, popping a
  notification, POSTing a webhook, or running a command on an alert transition is implemented in
  `dispatch.Dispatcher` but not yet called from anywhere runnable.
- **The watchlist matcher and SQLite history log are not yet wired in**, for the same reason —
  both are complete, tested modules (`watchlist.py`, `history.py`) with no caller yet in the fetch
  loop or CLI runners.
- **`filter.hide_ground`/`include_nonicao`/`min_alt_ft`/`max_alt_ft` aren't applied yet.** Only
  `filter.radius` and `filter.proximity` affect what you see today; the other four are validated
  config and drive the (also not-yet-wired) Filters screen's widget state.
- **No in-app settings UI or setup wizard.** Configure by hand-editing the TOML file or via CLI
  flags/environment variables.
- **No automatic FAA/tar1090 registry downloader.** You provide the CSV yourself — see
  [Owner lookup](#owner-lookup).
- **Non-US and military/LADD/PIA aircraft only get an owner if your receiver's own enrichment
  provides one.** This isn't a paid-API problem — free `--db-file` enrichment from
  `readsb`/`dump1090-fa` already carries registration/type/operator/flags for a wide range of
  aircraft the FAA registry alone can't cover — but it does depend on how your receiver is set up,
  not on anything `adsbtui` can add on its own.

---

## Contributing

Contributions are welcome. This codebase favors small, pure, unit-tested modules — no curses
calls, no network calls, and no global state outside of `ui/app.py`, `sources/`, and `dispatch.py`
where those are unavoidable — so most changes should come with tests that don't need a terminal or
a live feed. Run `python3 -m pytest` and `python3 -m ruff check src tests` before sending a change.

---

## License

Released under [CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/) — to the
extent possible under law, no copyright is asserted over this work, and you may copy, modify, and
distribute it, commercially or not, without asking permission. CC0 does **not** waive any
trademark or patent rights that may separately apply, and it disclaims all warranties; it is a
public-domain-equivalent dedication of copyright, not a blanket "no restrictions of any kind"
grant.
