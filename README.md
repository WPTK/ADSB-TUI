# ADSB-TUI

A terminal UI for tracking nearby aircraft from a `readsb`/`dump1090-fa` ADS-B feed. Live radar
scope plus a sortable table: distance, bearing and closest-approach from your location, rows
colored by altitude and by how alarmed you should be, and alerts (bell, desktop notification,
webhook, shell command) when something's worth noticing. Zero third-party dependencies.
Python 3.11+.

---

## Install

```bash
git clone https://github.com/WPTK/ADSB-TUI.git
cd ADSB-TUI
pip install -e .
```

- Windows needs curses: `pip install -e ".[windows]"`
- Tests/lint: `pip install -e ".[dev]"`, then `pytest -q` and `ruff check src tests`

---

## Quick start

Just run `adsbtui`. With no config yet, at a real terminal, a setup wizard finds your receiver,
reads its home location, and writes `~/.config/adsbtui/config.toml` for you.

To skip the wizard, write the config yourself:

```toml
[source]
url = "192.168.3.80"

[home]
lat = 37.7749
lon = -122.4194
```

Or drive it from flags with no config file at all:

```bash
adsbtui --url 192.168.3.80 --lat 37.7749 --lon -122.4194
```

### Pointing at your receiver

**Just give it the address.** A bare IP or hostname is enough: `adsbtui` tries the paths that
receiver images actually publish `aircraft.json` at and keeps the one that answers.

| What you can pass | Meaning |
|---|---|
| `192.168.3.80`, `adsb.local`, `192.168.3.80:8080` | Probe this receiver for aircraft.json |
| `http://<ip>/tar1090/data/aircraft.json` | An exact URL, used as-is |
| `/run/readsb/aircraft.json` | A local file, when `adsbtui` runs on the receiver itself |

The paths probed, in order, on port 80 and then 8080: `/tar1090/data/`, `/skyaware/data/`,
`/dump1090-fa/data/`, `/data/`. If none answer, the error lists every URL it tried.

---

## Features

- **Radar scope.** Aircraft plotted around you with range rings, compass ticks and an arrow per
  contact showing where it is heading. Appears automatically on terminals 110 columns and wider;
  `r` toggles it.
- **Altitude-colored rows.** Warm low, cool high, so circuit traffic and airliners separate at a
  glance without reading a single number.
- A background thread fetches data, so the UI never blocks on the network.
- Correct units (the original tool understated speed by ~46%).
- Resize-safe table; drops low-priority columns as the terminal narrows.
- Sort by any of the 14 columns (`s`).
- Alert grading (`NONE` → `EMERGENCY`) with bell/desktop/webhook/command delivery, per-aircraft
  cooldown, and quiet hours.
- **The registry downloads itself.** Registration, type and owner come from a local database that
  is fetched and refreshed daily in the background. Nothing to install, convert, or remember.
- Filters, live search, a watchlist, sighting history (SQLite), and a full in-app Settings
  screen (`F2`). Nothing requires hand-editing the config file.
- `--once` / `--watch` / `--headless` / `--batch` / `--check` for scripts and cron/systemd, zero
  curses dependency.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` / `?` | Help |
| `F2` / `,` | Settings: every config key, validated, saves to `config.toml` and applies live |
| `F8` / `D` | Data: download or rebuild the registry database, with progress |
| `Enter` / `d` | Detail pane for the selected aircraft |
| `↑`/`↓`, `k`/`j`, `PgUp`/`PgDn`, `Home`/`End` | Move / scroll |
| `s` / `F5` | Sort: any column; `S` reverses without opening it |
| `f` / `F4` / `/` | Filters and search |
| `c` / `F6` | Column chooser |
| `w` / `F7` | Watchlist editor; `w` inside it quick-adds the selected aircraft |
| `r` | Show/hide the radar scope |
| `u` | Cycle units: imperial / metric / aviation |
| `p` / `P` | Pause / resume |
| `+` / `-` | Grow / shrink filter radius |
| `q` / `Q` / `F10` | Quit |

`Esc` cancels a screen, `Enter` applies. Settings and Watchlist edits save to disk; Sort/Filters/
Columns apply live but revert on restart unless also saved via Settings.

---

## Configuration

Precedence: CLI flag > `ADSBTUI_<SECTION>_<KEY>` env var > `config.toml` > default. Every key
below is also in the Settings screen (`F2`) with the same default and a one-line help text.
Keys marked **†** are stored, validated, and editable, but don't affect behavior yet. See
[Known gaps](#known-gaps).

**`[source]`**

| Key | Default | Meaning |
|---|---|---|
| `url` | `""` (required) | `aircraft.json`: URL, bare path, or `file://` |
| `refresh_s` | `5.0` | Seconds between polls |
| `timeout_s` | `5.0` | Per-fetch timeout |
| `stale_s` † | `30.0` | Max age before the *source* counts as stale |
| `backoff_max_s` | `30.0` | Cap on fetch-retry backoff |
| `max_bytes` | `8000000` | Reject a larger response |

**`[home]`**: `lat` / `lon` (`0.0`, required; `(0,0)` is rejected as an unedited placeholder)

**`[filter]`**

| Key | Default | Meaning |
|---|---|---|
| `radius` | `15.0` mi | Drop aircraft farther than this |
| `proximity` | `5.0` mi | `OVERHEAD` alert threshold |
| `hide_ground` | `false` | Hide aircraft on the ground |
| `include_nonicao` | `true` | Include `~`-prefixed TIS-B/MLAT addresses |
| `min_alt_ft` / `max_alt_ft` | `0` / `60000` | Altitude band |

**`[display]`**

| Key | Default | Meaning |
|---|---|---|
| `units` | `"imperial"` | `imperial` / `metric` / `aviation` |
| `theme` † | `"default"` | Only `"default"` exists today |
| `color` | `"auto"` | `auto` / `always` / `never` |
| `columns` | 13 defaults | Which columns, in what order (`hex` is also available) |
| `owner_width` | `30` | Owner column width |
| `borders` | `"unicode"` | `unicode` / `ascii` / `none` |
| `density` † | `"normal"` | Not read by the renderer yet |
| `sort_key` | `"distance"` | Any column, see [Sortable fields](#sortable-fields) |
| `sort_reverse` | `false` | Reverse the sort |
| `stale_after_s` | `15.0` | Dim a row after this long with no position update |
| `linger_s` | `30.0` | Keep a dropped aircraft on screen (marked lost) this long |
| `status_bar` | `true` | Show the bottom status line |
| `vs_threshold_fpm` † | `256.0` | Renderer uses its own identical hardcoded value |

**`[alerts]`**

| Key | Default | Meaning |
|---|---|---|
| `emergency` | `true` | Grade a watched squawk / reported emergency as `EMERGENCY` |
| `squawks` | `[7500, 7600, 7700]` | Squawks graded as emergencies |
| `emergency_ignore_radius` | `true` | Let an emergency aircraft bypass `filter.radius` |
| `cpa_enabled` | `true` | Enable the closest-approach `INBOUND` projection |
| `cpa_distance` | `1.0` mi | CPA distance threshold |
| `cpa_horizon_s` | `600.0` | How far ahead a CPA solution still counts |
| `cpa_min_gs_kt` | `30.0` | Minimum speed before CPA applies |
| `events` | proximity/cpa/watchlist/emergency | Which events dispatch fires on |
| `bell` | `true` | Terminal bell (curses UI only) |
| `desktop` | `false` | `notify-send` / `osascript` |
| `webhook_url` / `webhook_format` | `""` / `"auto"` | `auto`/`json`/`ntfy`/`discord` |
| `command` | `""` | Shell command; gets `HEX`/`REG`/`CALL`/`DIST`/`ALT` in its env |
| `cooldown_s` | `600.0` | Min seconds between repeat alerts per aircraft |
| `quiet_hours` | `""` | `"HH:MM-HH:MM"`; suppresses bell/desktop only |

```toml
[alerts]
webhook_url = "https://ntfy.sh/my-adsb-alerts"
webhook_format = "ntfy"
quiet_hours = "22:00-07:00"
```

**`[watchlist]`**: `path` (default `~/.config/adsbtui/watchlist.txt`, see below); `pin_top` †,
`ignore_radius` † (stored, not yet applied)

**`[registry]`**: `db` (SQLite path), `max_age_days` (`1`, how old before a background refresh),
`auto_update` (`true`). See [Aircraft data](#aircraft-data-owner-type-country).

**`[history]`**: `db` (`""` disables sighting history), `retention_days` (`365`)

**`[logging]`**: `file`, `level`, `max_bytes`, `backup_count`, `redact_home` † (not yet applied)

**CLI flags**: `--config`, `--url`, `--lat`, `--lon`, `--radius`, `--proximity`, `--refresh`,
`--log-file`, `--log-level`, `--units`, `--no-color`, `--debug`, `--once`,
`--watch N`, `--headless`, `--batch`, `--format {table,json,csv}`, `--check`. Run `adsbtui --help`.

---

## Columns

| Header | Meaning |
|---|---|
| FLIGHT | Callsign the aircraft is transmitting |
| TAIL | Registration, e.g. N12345 |
| TYPE | ICAO type code, e.g. B738 for a 737-800 |
| ALTITUDE | Height above sea level; `GND` means on the ground |
| CLIMB | Climb or descent rate, with an arrow for the direction |
| SPEED | Ground speed: speed over the ground, not through the air |
| DISTANCE | How far the aircraft is from you |
| DIRECTION | Compass direction FROM you TO the aircraft |
| CLOSEST PASS | How close it will get, and in how long, if it holds course |
| OWNER | Registered owner or operator |
| FLAGS | `MIL` military, `PIA` private address, `LADD` limited display, plus size class |
| AGE | Time since its position last updated |
| ALERT | `OVHD` overhead, `INBND` closing on you, `EMERG` emergency squawk |
| ICAO | The 24-bit address that uniquely identifies the airframe |

Headers shorten (`ALTITUDE` to `ALT`) on a narrow terminal, and the same table is in the help
screen (`F1`). Column choice and order are yours via `c`.

## Sortable fields

`display.sort_key` accepts any of: `distance`, `altitude`, `callsign`, `reg`, `type`, `gs`
(ground speed), `vs` (vertical speed), `brg` (bearing), `cpa` (closest approach), `owner`,
`flags`, `age` (position age), `alert`, `hex`. Pick one from the Sort screen (`s`) rather than
typing it. Aircraft with no value for the chosen field always sort last.

---

## Aircraft data (owner, type, country)

Two sources, each filling in only what the one before it left blank:

1. **Your receiver.** Run `readsb`/`dump1090-fa` with `--db-file` and the feed itself carries
   registration, type, description, operator, and military/PIA/LADD flags. Nothing to set up.
2. **The local registry database.** Downloaded and rebuilt automatically in the background when
   it is missing or more than `registry.max_age_days` old (daily by default), from
   [tar1090-db](https://github.com/wiedehopf/tar1090-db) (global, **non-commercial use only**).
   `F8` shows what you have, how old it is, and forces a rebuild.

Anything still unknown falls back to what the ICAO address itself implies: a US N-number and a
country of registration, computed with no data files at all.

## Watchlist

`watchlist.path` is a plain-text file, one pattern per line: `prefix:pattern`, `prefix` one of
`hex`/`reg`/`call`/`owner`/`type`, `pattern` a case-insensitive shell glob:

```
hex:a004b3
reg:N7*
call:UAL*
type:C172
```

Edit from the Watchlist screen (`w`/`F7`); `w` inside it adds the selected aircraft directly. A
match sets `is_watched` (shown in `flags`) and fires the `watchlist` alert event once.

---

## Non-interactive modes

Zero curses dependency, safe for a script, cron job, or systemd unit.

```bash
adsbtui --once                     # fetch once, print a table, exit (4 on error)
adsbtui --once --format json       # or csv
adsbtui --watch 5                  # fetch/print/sleep, 5 cycles
adsbtui --headless                 # full pipeline as a background service: tracks, alerts,
                                    # dispatches, and records history, logged instead of drawn
adsbtui --batch                    # streams SEEN/NEW/LOST/ALERT lines, for piping
adsbtui --check                    # validate config, fetch once, print OK/ERROR
```

The setup wizard never runs in any of these, or off a non-interactive terminal. A broken config
gets the plain error and exit code 2 instead.

---

## Known gaps

- `display.theme`, `display.density`, `display.vs_threshold_fpm`: stored and editable, not yet
  read by the renderer.
- `watchlist.pin_top`, `watchlist.ignore_radius`: a match is flagged and alerts once, but isn't
  pinned to the top or exempted from `filter.radius`.
- `source.stale_s`, `logging.redact_home`: not yet applied.
- No CLI subcommand to force a registry rebuild; the automatic refresh and `F8` cover it.

---

## License

[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/): public-domain-equivalent,
no warranty. It doesn't waive any trademark or patent rights that separately apply.

Data pulled in via the Data screen carries its own terms, shown on-screen before download: the
FAA registry extract is public domain, tar1090-db is **non-commercial use only**. Neither is
bundled with this repository.
