# Configuration reference


Precedence: CLI flag > `ADSBTUI_<SECTION>_<KEY>` env var > `config.toml` > default. Every key
below is also in the Settings screen (`F2`) with the same default and a one-line help text.
Keys marked **†** are stored, validated, and editable, but don't affect behavior yet. See
[Known gaps](../README.md#known-gaps).

**Which file is read.** The first that exists of: `--config PATH`, `$ADSBTUI_CONFIG`,
`$XDG_CONFIG_HOME/adsbtui/config.toml` (`XDG_CONFIG_HOME` defaults to `~/.config`). The current
directory is never searched, because a config can set `alerts.command`. An explicit path that
cannot be read is an error; with no file at all, defaults, environment and flags apply.
Old files that still set a retired key (`display.theme`, `display.density`,
`display.vs_threshold_fpm`, `watchlist.pin_top`, `watchlist.ignore_radius`,
`logging.redact_home`) load without complaint and the key is ignored.

**`[source]`**

| Key | Default | Meaning |
|---|---|---|
| `url` | `""` (required) | `aircraft.json`: URL, bare path, or `file://` |
| `refresh_s` | `5.0` | Seconds between polls |
| `timeout_s` | `5.0` | Wall-clock limit for one HTTP fetch, from request to the last byte of the body |
| `stale_s` † | `30.0` | Max age before the *source* counts as stale |
| `backoff_max_s` | `30.0` | Cap on fetch-retry backoff |
| `max_bytes` | `8000000` | Reject a larger response |

**`[home]`**: `lat` / `lon` (`0.0`, required; `(0,0)` is rejected as an unedited placeholder)

**`[filter]`**

| Key | Default | Meaning |
|---|---|---|
| `radius` | `15.0` mi | Drop aircraft farther than this |
| `proximity` | `5.0` mi | `OVERHEAD` alert threshold (aircraft on the ground show `NEAR` and never alert) |
| `hide_ground` | `false` | Hide aircraft on the ground |
| `include_nonicao` | `true` | Include `~`-prefixed TIS-B/MLAT addresses |
| `min_alt_ft` / `max_alt_ft` | `0` / `60000` | Altitude band |

**`[display]`**

| Key | Default | Meaning |
|---|---|---|
| `units` | `"imperial"` | `imperial` / `metric` / `aviation` |
| `color` | `"auto"` | `auto` / `always` / `never` |
| `columns` | 13 defaults | Which columns, in what order (`hex` is also available) |
| `owner_width` | `30` | Owner column width |
| `borders` | `"unicode"` | `unicode` / `ascii` / `none` |
| `sort_key` | `"distance"` | Any column, see [Sortable fields](#sortable-fields) |
| `sort_reverse` | `false` | Reverse the sort |
| `stale_after_s` | `15.0` | Dim a row after this long with no position update |
| `linger_s` | `30.0` | Keep a dropped aircraft on screen (marked lost) this long |
| `status_bar` | `true` | Show the bottom status line |

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

**`[watchlist]`**: `path` (default `~/.config/adsbtui/watchlist.txt`, see below)

**`[registry]`**: `db` (SQLite path), `max_age_days` (`1`, how old before a background refresh),
`auto_update` (`true`). See [Aircraft data](#aircraft-data-owner-type-country).

**`[history]`**: `db` (`""` disables sighting history), `retention_days` (`365`)

**`[logging]`**: `file`, `level`, `max_bytes`, `backup_count`

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
| ALERT | `OVHD` overhead, `NEAR` on the ground nearby (never alerts), `INBND` closing on you, `EMERG` emergency squawk |
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
   `F8` shows what you have, how old it is, and forces a rebuild. It can also download the
   FAA registry (US only, public domain).

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
