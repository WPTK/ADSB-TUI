# ADSB-TUI

ADSB-TUI is a zero-dependency, Python 3.11+ terminal UI for tracking nearby aircraft from a
`readsb`/`dump1090-fa`-compatible ADS-B feed. It polls your receiver's `aircraft.json` in the
background, converts every unit correctly, works out each aircraft's distance and bearing from
your location, projects a closest-point-of-approach for aircraft heading your way, colors the
table by how alarmed you should be, and can ring a bell, push a desktop notification, hit a
webhook, or run a shell command when something worth noticing happens — all without ever
blocking the display on the network.

> **Status:** this is a ground-up rewrite of the original 181-line `adsbtui.py` script into a
> tested package (see `AUDIT.md`/`CHANGELOG.md` for why). A first run with no config file drops
> you into an in-app setup wizard instead of an error message. See
> [Known limitations](#known-limitations) for the handful of settings that are stored and
> editable but not yet acted on.

---

## Features

- **Background fetch thread**: the curses UI never blocks on the network; a daemon thread polls
  on `source.refresh_s` with exponential backoff on failure, and the main thread only drains a
  queue and redraws when something actually changed.
- **First-run setup wizard**: with no config file yet, launching `adsbtui` at a terminal starts a
  short wizard instead of failing — it probes your receiver for the handful of paths real
  images actually serve `aircraft.json` at, offers to read your home coordinates from its
  `receiver.json`, and writes `~/.config/adsbtui/config.toml` for you. Scripted runs
  (`--once`/`--watch`/`--headless`/`--batch`/`--check`), non-interactive terminals, and an
  explicit `--config` that fails to load all get the plain error instead.
- **Correct units**: ground speed is stored internally in knots and converted properly to mph/
  km/h/kt for display — the original tool's bug silently understated speed by ~46%.
- **Resize-safe, crash-resistant table**: every draw call is guarded against `curses.error`, the
  UI degrades to a "Terminal too small" message below a sane minimum size instead of crashing,
  and a configurable, terminal-width-aware set of columns (flight, registration, type, altitude,
  vertical-speed trend, ground speed, distance, bearing, closest-point-of-approach, owner, MIL/
  PIA/LADD/category flags, position age, alert, raw hex) drops least-useful columns first as the
  terminal narrows.
- **Alert grading and delivery**: every aircraft is graded `NONE`/`OUTBOUND`/`INBOUND`/
  `OVERHEAD`/`EMERGENCY` from a priority-ordered set of checks — a watched emergency squawk or
  reported emergency state, currently inside your proximity radius, projected to close within a
  configurable closest-point-of-approach distance and time horizon, or recently close and now
  receding. A level transition (or a watchlist match) fires the terminal bell, a desktop
  notification, a webhook, and/or a shell command, with a per-aircraft cooldown and a quiet-hours
  window, all from the background thread so a slow webhook can't stall the display.
- **Owner and type lookup**: registration, aircraft type, description, and owner/operator come
  from readsb/dump1090's own `--db-file` enrichment fields when your receiver provides them,
  falling back to a locally-built SQLite registry (managed from the in-app Data screen), an
  optional hand-supplied FAA CSV, and finally a derived US N-number and country worked out from
  the hex code alone.
- **Filters and search**: hide aircraft on the ground, include/exclude non-ICAO (TIS-B/MLAT)
  addresses, restrict to an altitude band, and search live across callsign, hex, registration,
  owner, and type — all from the Filters screen (`f`).
- **Watchlist**: a plain-text file of `hex:`/`reg:`/`call:`/`owner:`/`type:` glob patterns,
  editable from an in-app screen (`w`) that can also add the currently-selected aircraft with one
  key. A match is exposed as `is_watched` and drives the `watchlist` alert event.
- **Sighting history**: when a track ends, a summary (closest approach, highest altitude, fastest
  speed, squawks seen, whether it declared an emergency) is written to a SQLite database, with
  retention pruning at startup.
- **In-app settings**: a tabbed Settings screen (`F2`) covers nearly every key in the config
  schema, with the current value, the default, and a one-line explanation for each field; saving
  writes `config.toml` and applies the change to the running session immediately.
- **Non-interactive modes**: `--once`, `--watch N`, `--headless`, and `--batch`, each with zero
  curses dependency — for cron jobs, systemd services, and shell pipelines. `--headless` runs the
  full pipeline (tracking, alert dispatch, history) as a background service; `--batch` streams
  `SEEN`/`NEW`/`LOST`/`ALERT` lines for scripting.
- **Live controls**: pause the display, cycle unit systems, sort by distance/altitude/callsign,
  or grow/shrink the filter radius, all without restarting or touching a config file.

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

Run `adsbtui` with nothing configured yet, at an interactive terminal, and the setup wizard takes
over: it asks for your receiver's hostname and probes the common `aircraft.json` locations for
you, offers to read your home coordinates from the receiver's `receiver.json` (or lets you type
them), asks for a unit system and filter radius, and writes the result to
`~/.config/adsbtui/config.toml` before dropping you into the table. Press `Esc` at any point to
cancel and fall back to the plain configuration error.

To skip the wizard, write the config file yourself:

```toml
[source]
url = "http://192.168.1.50/skyaware/data/aircraft.json"

[home]
lat = 37.7749
lon = -122.4194
```

```bash
adsbtui
```

Or drive it entirely from CLI flags, with no config file at all:

```bash
adsbtui --url http://192.168.1.50/skyaware/data/aircraft.json --lat 37.7749 --lon -122.4194
```

`home.lat`/`home.lon` are required — `(0.0, 0.0)` is treated as the unedited placeholder and
rejected — and `source.url` is required and must not be empty. (The wizard exists precisely to
fill these in without you having to know that up front.)

---

## Configuration reference

Precedence is **CLI flag > `ADSBTUI_*` environment variable > TOML file > the default below**.
Every key is parsed, type-coerced, and validated by `config.py`. Most are read from the in-app
Settings screen (`F2`) too, which shows the same default and help text as this table and writes
your changes back to the TOML file. A few keys are still write-only in this snapshot — stored,
validated, and editable, but not yet consumed anywhere that would change behavior — and are
marked "not yet applied" below; see [Known limitations](#known-limitations) for the consolidated
list and why.

Environment variable names follow `ADSBTUI_<SECTION>_<KEY>`, e.g. `ADSBTUI_HOME_LAT=37.7749`.

### `[source]`

| Key | Default | Meaning |
|---|---|---|
| `url` | `""` (required) | `aircraft.json` source: an `http://`/`https://` URL, a bare filesystem path, or a `file://` URL. |
| `refresh_s` | `5.0` | Seconds between polls. |
| `timeout_s` | `5.0` | Per-fetch timeout, in seconds. |
| `stale_s` | `30.0` | Intended max acceptable age of the feed's own data before the *source* (not an individual aircraft) is considered stale. Not yet applied. |
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
| `hide_ground` | `false` | Hide aircraft reporting `alt_baro == "ground"`. Editable from the Filters screen (`f`). |
| `include_nonicao` | `true` | Include `~`-prefixed TIS-B/MLAT addresses. Editable from the Filters screen. |
| `min_alt_ft` | `0` | Minimum-altitude filter, feet. Editable from the Filters screen. |
| `max_alt_ft` | `60000` | Maximum-altitude filter, feet. Editable from the Filters screen. |

### `[display]`

| Key | Default | Meaning |
|---|---|---|
| `units` | `"imperial"` | `imperial`, `metric`, or `aviation` — controls every displayed speed/altitude/distance unit. Cycle live with `u`. |
| `theme` | `"default"` | Reserved for a future selectable color theme; only `"default"` exists today. |
| `color` | `"auto"` | `auto`/`always`/`never`. `never` disables curses color (same effect as `--no-color`). |
| `columns` | `["flight","reg","type","alt","vs","gs","dist","brg","cpa","owner","flags","age","alert"]` | Which table columns to show, and in what order. `hex` is also available but not shown by default. Unknown keys are ignored; `flight` is never dropped even on a very narrow terminal. Editable from the Columns screen (`c`). |
| `owner_width` | `30` | Preferred width, in characters, of the owner column. |
| `borders` | `"unicode"` | `unicode`/`ascii`/`none` box-drawing style for the header divider and column separators. Editable from the Columns screen. |
| `density` | `"normal"` | `compact`/`normal`/`wide`. Stored and editable from the Columns screen; not yet read by the renderer. |
| `sort_key` | `"distance"` | `distance`, `altitude`, or `callsign`. Aircraft with no value for the chosen key always sort last. Editable from the Sort screen (`s`); `S` reverses without opening it. |
| `sort_reverse` | `false` | Reverse the sort order. |
| `stale_after_s` | `15.0` | Seconds since the last position update before a row is marked stale (dimmed). |
| `linger_s` | `30.0` | Seconds an aircraft that dropped out of the feed is still shown (marked lost) before being removed. |
| `status_bar` | `true` | Show/hide the bottom status line. |
| `vs_threshold_fpm` | `256.0` | Vertical-speed threshold, ft/min, for the climb/descend/level trend arrow. Stored and editable in Settings; the renderer currently uses its own identical hardcoded default rather than reading this field, so changing it here has no effect yet. |

### `[alerts]`

| Key | Default | Meaning |
|---|---|---|
| `emergency` | `true` | Grade a watched squawk or a reported emergency state as `EMERGENCY`. |
| `squawks` | `[7500, 7600, 7700]` | Squawk codes graded as emergencies. |
| `emergency_ignore_radius` | `true` | Let an emergency aircraft bypass `filter.radius`. |
| `cpa_enabled` | `true` | Enable the closest-point-of-approach `INBOUND` projection. |
| `cpa_distance` | `1.0` | Miles; CPA distance threshold for `INBOUND`. |
| `cpa_horizon_s` | `600.0` | Seconds; how far into the future a CPA solution can be and still count as `INBOUND`. |
| `cpa_min_gs_kt` | `30.0` | Minimum ground speed, knots, before a CPA projection counts (filters out effectively-parked aircraft). |
| `events` | `["proximity","cpa","watchlist","emergency"]` | Which event families the alert dispatcher acts on. |
| `bell` | `true` | Ring the terminal bell on a dispatched event (curses UI only; a no-op in `--headless`). |
| `desktop` | `false` | Send a desktop notification (`notify-send`/`osascript`) on a dispatched event. |
| `webhook_url` | `""` | POST a JSON/ntfy/Discord-formatted payload here on a dispatched event. |
| `webhook_format` | `"auto"` | `auto`/`json`/`ntfy`/`discord`. |
| `command` | `""` | Shell command (word-split, not run through a shell) executed with `HEX`/`REG`/`CALL`/`DIST`/`ALT` in its environment, on a dispatched event. |
| `cooldown_s` | `600.0` | Minimum seconds between repeat dispatches for the same (aircraft, event) pair. |
| `quiet_hours` | `""` | `"HH:MM-HH:MM"` window (may wrap past midnight) during which bell/desktop are suppressed; webhook/command still fire. A malformed value is treated as "no quiet hours". |

All of the above are live in both the curses UI and `--headless`; see
[Alerts configuration](#alerts-configuration) for a worked example.

### `[watchlist]`

| Key | Default | Meaning |
|---|---|---|
| `path` | `"~/.config/adsbtui/watchlist.txt"` | Plain-text watchlist file — see [Watchlist](#watchlist). Loaded at startup, editable from the Watchlist screen (`w`), and re-saved to this path on every edit. |
| `pin_top` | `true` | Intended to sort watchlist matches above everything else. Stored and editable in Settings; not yet applied — a match is still sorted by the normal `sort_key`. |
| `ignore_radius` | `true` | Intended to let a watchlist match bypass `filter.radius`. Stored and editable in Settings; not yet applied. |

### `[registry]`

| Key | Default | Meaning |
|---|---|---|
| `path` | `""` | Optional path to a legacy FAA-format owner CSV (`N-NUMBER,NAME,MODE S CODE HEX`) — see [Owner and type lookup](#owner-and-type-lookup). |
| `db` | `"~/.local/share/adsbtui/registry.sqlite"` | SQLite database built by the in-app Data screen (`F8`/`D`) from the FAA bulk registry and/or tar1090-db. Read by the enrichment chain if present. |
| `max_age_days` | `30` | The Data screen warns when a downloaded source is older than this. |

### `[history]`

| Key | Default | Meaning |
|---|---|---|
| `db` | `""` | Path to a SQLite database for sighting history. `""` disables history entirely (the default). Consumed by both the curses UI and `--headless`. |
| `retention_days` | `365` | Rows older than this are pruned at startup. |

### `[logging]`

| Key | Default | Meaning |
|---|---|---|
| `file` | `"~/.local/state/adsbtui/adsbtui.log"` | Rotating log file path. |
| `level` | `"INFO"` | `DEBUG`/`INFO`/`WARNING`/`ERROR`. |
| `max_bytes` | `1048576` | Rotate after this many bytes. |
| `backup_count` | `3` | Number of rotated backups to keep. |
| `redact_home` | `true` | Intended to keep home coordinates out of log output. Stored and editable in Settings; not yet applied. |

### CLI flags

`--config PATH`, `--url`, `--lat`, `--lon`, `--radius`, `--proximity`, `--refresh`, `--registry`,
`--log-file`, `--log-level {DEBUG,INFO,WARNING,ERROR}`, `--units {imperial,metric,aviation}`,
`--no-color`, `--debug` (shortcut for `--log-level DEBUG`) map onto the config keys above. `--once`,
`--watch N`, `--headless`, `--batch`, `--format {table,json,csv}`, and `--check` select a run mode
— see [Non-interactive modes](#non-interactive-modes). Run `adsbtui --help` for the full list.

There is no CLI subcommand for registry management (no `adsbtui registry update`) — downloading
or refreshing the FAA/tar1090-db registry is done from the in-app Data screen; see
[Owner and type lookup](#owner-and-type-lookup).

---

## Data sources

`adsbtui` reads whatever your receiver's `aircraft.json` looks like — it does not run or configure
`readsb`/`dump1090-fa` itself. Two ways to point `source.url` at it:

**Over HTTP, from another machine on your network.** Most receiver images run a small web server
alongside the decoder. Common paths, depending on your image (the setup wizard probes exactly
these):

- `http://<receiver-ip>/tar1090/data/aircraft.json` (tar1090)
- `http://<receiver-ip>/skyaware/data/aircraft.json` (FlightAware's SkyAware, dump1090-fa's usual
  lighttpd alias)
- `http://<receiver-ip>/dump1090-fa/data/aircraft.json` (dump1090-fa's own alias)
- `http://<receiver-ip>:8080/data/aircraft.json` (readsb's/dump1090's own built-in web server,
  default port 8080)

None of them serve `aircraft.json` at a raw `/run/readsb/` path over HTTP (that's a filesystem
path readsb writes to locally, not something a web server exposes by default).

**As a local file, when `adsbtui` runs on the receiver itself.** `readsb` writes `aircraft.json` to
a path like `/run/readsb/aircraft.json` continuously. Point `source.url` straight at it — a bare
path or a `file://` URL both work, and the setup wizard checks these two paths automatically:

```toml
[source]
url = "/run/readsb/aircraft.json"
# or: url = "file:///run/readsb/aircraft.json"
```

No `chmod`/`chown` dance is needed here either: run `adsbtui` as a user that can already read the
file (e.g. in the same group `readsb` writes it as), and normal filesystem permissions apply like
they would for any other program reading a file on the same box.

---

## Owner and type lookup

Registration, aircraft type, description, operator, and military/PIA/LADD flags are assembled
from several sources, each one only filling in what the previous one left blank:

1. **Your receiver's own enrichment.** If `readsb`/`dump1090-fa` is run with a `--db-file` pointed
   at their aircraft database, the feed itself carries `r`/`t`/`desc`/`ownOp`/`dbFlags` directly —
   `adsbtui` reads those fields with no extra download or configuration of its own, and they
   always win over anything looked up below.
2. **The local registry database**, at `registry.db` (default
   `~/.local/share/adsbtui/registry.sqlite`). Press `F8`/`D` in the running app to open the **Data
   screen**: it lists the FAA bulk registry and tar1090-db as available sources, shows whether
   each is on disk, its row count, and its age, and downloads/rebuilds either one on a background
   thread with a progress bar (`u` to update the selected source, `U` for all of them). The FAA
   extract is US-registered aircraft only and is public domain; tar1090-db is global but
   **non-commercial use only** — both terms are shown on the Data screen itself before you
   download anything.
3. **A legacy hand-supplied CSV**, if you set `registry.path` to one with (at least) two columns,
   `MODE S CODE HEX` and `NAME`. `tests/fixtures/sample_master.csv` is a 50-row example of the
   expected shape:

   ```csv
   N-NUMBER,NAME,MODE S CODE HEX
   100,BENE MARY D                                       ,A004B3
   10000,9AT LLC                                           ,A00725
   ```

   The real FAA `MASTER.txt` (inside `ReleasableAircraft.zip` from the
   [Releasable Aircraft Registry download page](https://www.faa.gov/licenses_certificates/aircraft_certification/aircraft_registry/releasable_aircraft_download))
   works unchanged — no conversion step needed — but for a full local database it's simpler to let
   the Data screen download and build it for you instead.
4. **A derived US N-number and country**, worked out purely from the hex code — no download
   required — as the last-resort fallback.

---

## Watchlist

`watchlist.path` (default `~/.config/adsbtui/watchlist.txt`) is a plain-text file, one pattern per
line: `prefix:pattern`, where `prefix` is one of `hex`, `reg`, `call`, `owner`, or `type`, and
`pattern` is a shell-style glob matched case-insensitively:

```
hex:a004b3
reg:N7*
call:UAL*
owner:*flight school*
type:C172
```

Blank lines and lines starting with `#` are comments. Open the Watchlist screen with `w`/`F7` to
add, remove, and enable/disable entries; pressing `w` again inside it adds the currently-selected
aircraft with a `hex:` pattern directly. Edits are written back to `watchlist.path` immediately. A
match sets `is_watched` on the aircraft (shown in the `flags` column) and fires the `watchlist`
alert event the first time it's seen. `pin_top` and `ignore_radius` are stored settings for a
watchlist match but not yet applied to sorting or filtering — see
[Known limitations](#known-limitations).

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` / `?` | Help overlay listing every binding |
| `F2` / `,` | Settings: every config key, grouped by section, with inline validation; saves to `config.toml` and applies live |
| `F8` / `D` | Data screen: registry sources, their age and row counts, and background download/rebuild |
| `Enter` / `d` | Detail pane for the selected aircraft (every field the feed provides) |
| `up` / `down`, `k` / `j` | Move the selection |
| `PgUp` / `PgDn`, `Home` / `End` | Scroll the table |
| `s` / `F5` | Sort screen (distance, altitude, or callsign) |
| `S` | Reverse the current sort without opening a screen |
| `f` / `F4` / `/` | Filters and search: ground traffic, non-ICAO addresses, altitude band, text search |
| `c` / `F6` | Column chooser: which columns, in what order, plus density and border style |
| `w` / `F7` | Watchlist editor: add, remove, and enable/disable patterns; `w` inside it quick-adds the selected aircraft |
| `u` | Cycle units: imperial, metric, aviation |
| `p` / `P` | Pause / resume the display (the fetch thread keeps running in the background) |
| `+` / `-` | Grow / shrink the filter radius by 1 mile |
| `q` / `Q` / `F10` | Quit |
| *(terminal resize)* | Redraws at the new size automatically |

Inside any screen, `Esc` cancels and `Enter` applies. Sort, filters, columns, and settings changes
apply to the running session immediately; Settings and Watchlist edits are also written back to
disk, while Sort/Filters/Columns changes made this way revert to your config file on restart
unless you also open Settings and save.

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

# Run forever with no stdout output: fetch, track, grade alerts, dispatch
# notifications, and record sighting history, exactly like the curses UI does,
# logging one line per cycle to logging.file instead of drawing anything.
adsbtui --headless

# Stream SEEN/NEW/LOST/ALERT lines as aircraft appear, disappear, and change
# alert level, for scripting/piping. No bell/webhook/command fires here -- the
# pipe IS the notification channel.
adsbtui --batch

# Validate config, fetch once, and report OK/ERROR without printing any aircraft data.
adsbtui --check
```

The wizard never runs in any of these modes, or when stdin/stdout isn't a terminal — a missing or
broken config is reported as a plain error with exit code 2 instead.

---

## Alerts configuration

Every key below is live in both the curses UI and `--headless`: `emergency`/`squawks` and the
`cpa_*` keys drive `alert_level` (shown in the table's `alert` column and in every non-interactive
output format), and a level transition or a watchlist match runs through `bell`/`desktop`/
`webhook_url`/`command` with the cooldown and quiet-hours settings applied.

```toml
[alerts]
emergency = true
squawks = [7500, 7600, 7700]
cpa_enabled = true
cpa_distance = 1.0
cpa_horizon_s = 600
cpa_min_gs_kt = 30

events = ["proximity", "cpa", "emergency", "watchlist"]
bell = true
desktop = true
webhook_url = "https://ntfy.sh/my-adsb-alerts"
webhook_format = "ntfy"
command = ""
cooldown_s = 600
quiet_hours = "22:00-07:00"
```

`command`, if set, is word-split (not run through a shell) and given `HEX`/`REG`/`CALL`/`DIST`/
`ALT` in its environment — e.g. `command = "/usr/local/bin/notify-me.sh"`.

---

## Known limitations

Being direct about the state of this snapshot — these are all stored, validated config keys that
show up (with a note) in the Settings screen, but don't yet change behavior:

- **`display.theme`**: only `"default"` exists; the key is reserved for a future selectable
  color theme.
- **`display.density`** and **`display.vs_threshold_fpm`**: editable, but the renderer doesn't
  read either one yet (vertical-speed classification uses its own identical hardcoded default).
- **`watchlist.pin_top`** and **`watchlist.ignore_radius`**: a watchlist match is flagged
  (`is_watched`) and fires an alert event, but is not pinned to the top of the table and does not
  bypass `filter.radius`.
- **`source.stale_s`** and **`logging.redact_home`**: not yet applied.
- **No CLI subcommand for registry management.** Downloading or rebuilding the FAA/tar1090-db
  registry is done from the in-app Data screen (`F8`/`D`); there is no headless equivalent yet, so
  a fully unattended first deployment needs one interactive session to populate
  `registry.db` before switching to `--headless`.

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

Aircraft registry data pulled in via the Data screen carries its own terms: the FAA registry
extract is public domain; tar1090-db is **non-commercial use only**. Both are stated on the Data
screen itself before download, and neither is bundled with this repository.
