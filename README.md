# ADSB-TUI

A terminal UI for tracking nearby aircraft from a `readsb`/`dump1090-fa` ADS-B feed. Polls your
receiver in the background, shows distance/bearing/closest-approach from your location, colors
rows by how alarmed you should be, and can alert you (bell, desktop notification, webhook, shell
command) when something's worth noticing. Zero third-party dependencies. Python 3.11+.

---

## Install

```bash
git clone https://github.com/WPTK/ADSB-TUI.git
cd ADSB-TUI
pip install -e .
```

- Windows needs curses: `pip install -e ".[windows]"`
- Tests/lint: `pip install -e ".[dev]"` — then `pytest -q` and `ruff check src tests`

---

## Quick start

Just run `adsbtui`. With no config yet, at a real terminal, a setup wizard finds your receiver,
reads its home location, and writes `~/.config/adsbtui/config.toml` for you.

To skip the wizard, write the config yourself:

```toml
[source]
url = "http://192.168.1.50/skyaware/data/aircraft.json"

[home]
lat = 37.7749
lon = -122.4194
```

Or drive it from flags with no config file at all:

```bash
adsbtui --url http://192.168.1.50/skyaware/data/aircraft.json --lat 37.7749 --lon -122.4194
```

**Pointing at your receiver** — the wizard probes these automatically; pick whichever answers:

| URL | Image |
|---|---|
| `http://<ip>/tar1090/data/aircraft.json` | tar1090 |
| `http://<ip>/skyaware/data/aircraft.json` | FlightAware SkyAware / dump1090-fa |
| `http://<ip>/dump1090-fa/data/aircraft.json` | dump1090-fa |
| `http://<ip>:8080/data/aircraft.json` | readsb / dump1090 built-in server |
| `/run/readsb/aircraft.json` | local file, when `adsbtui` runs on the receiver itself |

---

## Features

- Background fetch thread — the UI never blocks on the network.
- Correct units (the original tool understated speed by ~46%).
- Resize-safe table; drops low-priority columns as the terminal narrows.
- **Every column is sortable** (`s`), not just distance/altitude/callsign.
- Alert grading (`NONE` → `EMERGENCY`) with bell/desktop/webhook/command delivery, per-aircraft
  cooldown, and quiet hours.
- Registration/type/owner from your receiver's own feed first, then an auto-downloaded local
  database (`F8`), with a derived US N-number and country as a no-download fallback.
- Filters, live search, a watchlist, sighting history (SQLite), and a full in-app Settings
  screen (`F2`) — nothing requires hand-editing the config file.
- `--once` / `--watch` / `--headless` / `--batch` / `--check` for scripts and cron/systemd, zero
  curses dependency.

---

## Keyboard shortcuts

| Key | Action |
|---|---|
| `F1` / `?` | Help |
| `F2` / `,` | Settings — every config key, validated, saves to `config.toml` and applies live |
| `F8` / `D` | Data — download/rebuild the registry database, with progress |
| `Enter` / `d` | Detail pane for the selected aircraft |
| `↑`/`↓`, `k`/`j`, `PgUp`/`PgDn`, `Home`/`End` | Move / scroll |
| `s` / `F5` | Sort — any column; `S` reverses without opening it |
| `f` / `F4` / `/` | Filters and search |
| `c` / `F6` | Column chooser |
| `w` / `F7` | Watchlist editor; `w` inside it quick-adds the selected aircraft |
| `u` | Cycle units: imperial / metric / aviation |
| `p` / `P` | Pause / resume |
| `+` / `-` | Grow / shrink filter radius |
| `q` / `Q` / `F10` | Quit |

`Esc` cancels a screen, `Enter` applies. Settings and Watchlist edits save to disk; Sort/Filters/
Columns apply live but revert on restart unless also saved via Settings.

---

## Configuration

Precedence: **CLI flag > `ADSBTUI_<SECTION>_<KEY>` env var > `config.toml` > default.** Every key
below is also in the Settings screen (`F2`) with the same default and a one-line help text.
Keys marked **†** are stored/validated/editable but don't affect behavior yet — see
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

**`[home]`** — `lat` / `lon` (`0.0`, required, `(0,0)` is rejected as an unedited placeholder)

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
| `sort_key` | `"distance"` | Any column — see [Sortable fields](#sortable-fields) |
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

**`[watchlist]`** — `path` (default `~/.config/adsbtui/watchlist.txt`, see below); `pin_top` †,
`ignore_radius` † (stored, not yet applied)

**`[registry]`** — see [Aircraft data](#aircraft-data-owner-type-country):
`db` (SQLite path, managed by the Data screen), `max_age_days` (`30`, staleness warning),
`path` (legacy bring-your-own CSV, off by default)

**`[history]`** — `db` (`""` disables sighting history), `retention_days` (`365`)

**`[logging]`** — `file`, `level`, `max_bytes`, `backup_count`, `redact_home` † (not yet applied)

**CLI flags**: `--config`, `--url`, `--lat`, `--lon`, `--radius`, `--proximity`, `--refresh`,
`--registry`, `--log-file`, `--log-level`, `--units`, `--no-color`, `--debug`, `--once`,
`--watch N`, `--headless`, `--batch`, `--format {table,json,csv}`, `--check`. Run `adsbtui --help`.

---

## Sortable fields

`display.sort_key` accepts any of: `distance`, `altitude`, `callsign`, `reg`, `type`, `gs`
(ground speed), `vs` (vertical speed), `brg` (bearing), `cpa` (closest approach), `owner`,
`flags`, `age` (position age), `alert`, `hex`. Pick one from the Sort screen (`s`) rather than
typing it — aircraft with no value for the chosen field always sort last.

---

## Aircraft data (owner, type, country)

Three sources, each filling in only what the one before it left blank:

1. **Your receiver.** Run `readsb`/`dump1090-fa` with `--db-file` and the feed itself carries
   registration, type, description, operator, and military/PIA/LADD flags — nothing to set up
   here.
2. **Auto-downloaded database** (`F8`/`D`, the Data screen). Downloads and builds a local SQLite
   registry from the FAA bulk registry (US, public domain) or
   [tar1090-db](https://github.com/wiedehopf/tar1090-db) (global, **non-commercial use only** —
   shown on-screen before you download). This is the normal path; no manual file wrangling.
3. **Bring-your-own CSV** (`registry.path`, legacy). Only needed if you already have an
   FAA-format CSV (`N-NUMBER,NAME,MODE S CODE HEX`) and don't want #2's download. Superseded by
   #2 for a fresh setup — there's no reason to reach for this otherwise.

There's no CLI subcommand for #2 (no `adsbtui registry update`) — it's a Data-screen-only action
for now, so a fully headless first deployment needs one interactive session first.

---

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

Zero curses dependency — safe for a script, cron job, or systemd unit.

```bash
adsbtui --once                     # fetch once, print a table, exit (4 on error)
adsbtui --once --format json       # or csv
adsbtui --watch 5                  # fetch/print/sleep, 5 cycles
adsbtui --headless                 # full pipeline as a background service: tracks, alerts,
                                    # dispatches, and records history, logged instead of drawn
adsbtui --batch                    # streams SEEN/NEW/LOST/ALERT lines, for piping
adsbtui --check                    # validate config, fetch once, print OK/ERROR
```

The setup wizard never runs in any of these, or off a non-interactive terminal — a broken config
gets the plain error and exit code 2 instead.

---

## Known gaps

- `display.theme`, `display.density`, `display.vs_threshold_fpm` — stored and editable, not yet
  read by the renderer.
- `watchlist.pin_top`, `watchlist.ignore_radius` — a match is flagged and alerts once, but isn't
  pinned to the top or exempted from `filter.radius`.
- `source.stale_s`, `logging.redact_home` — not yet applied.
- No CLI registry-management subcommand — see [Aircraft data](#aircraft-data-owner-type-country).

---

## License

[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/) — public-domain-equivalent;
no warranty, and CC0 doesn't waive any trademark/patent rights that separately apply.

Data pulled in via the Data screen carries its own terms, shown on-screen before download: the
FAA registry extract is public domain, tar1090-db is **non-commercial use only**. Neither is
bundled with this repository.
