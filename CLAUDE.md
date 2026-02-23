# CLAUDE.md - AI Assistant Guide for ADSB-TUI

## Project Overview

ADSB-TUI is a Python terminal user interface (TUI) application that tracks aircraft in real-time using data from an ADS-B (Automatic Dependent Surveillance-Broadcast) receiver. It connects to a `dump1090`-compatible receiver over HTTP, enriches aircraft data with FAA ownership information from a local CSV file, and displays everything in a retro-styled terminal table.

**License:** Creative Commons Zero (CC0) - full public domain.

---

## Repository Structure

```
/home/user/ADSB-TUI/
├── adsbtui.py      # Entire application - single-file Python script (182 lines)
├── MASTER.csv      # FAA Aircraft Registration Database (~20MB, ~300K records)
├── README.md       # User-facing documentation with setup instructions
├── LICENSE         # CC0 1.0 Universal license text
└── CLAUDE.md       # This file
```

This is a deliberately minimal project. There are no subdirectories, no package structure, no build system, and no test suite. All logic lives in `adsbtui.py`.

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.6+ |
| Terminal UI | `curses` (stdlib) |
| HTTP requests | `requests` |
| CSV/data processing | `pandas` |
| Math/geo | `math` (stdlib, haversine formula) |
| Logging | `logging` (stdlib) to `adsb_tracker.log` |

**Installing dependencies:**
```bash
pip install pandas requests
# On Windows only:
pip install windows-curses
```

There is no `requirements.txt`. Dependencies are documented only in the README.

---

## Configuration

All configuration is hardcoded at the top of `adsbtui.py` (lines 13-21). Users must edit the file directly - there is no config file or environment variable support.

```python
TRACKER_LAT = 0.0          # Your latitude (decimal degrees)
TRACKER_LON = 0.0          # Your longitude (decimal degrees)
DUMP1090_URL = "http://<DUMP1090_SERVER>/run/readsb/aircraft.json"
MASTER_CSV_PATH = "./master.csv"   # Relative or absolute path to FAA CSV
KM_TO_MI = 0.621371        # Unit conversion constant - do not change
RADIUS_LIMIT = 15          # Tracking radius in miles
PROXIMITY_ALERT = 5        # Alert threshold in miles (displayed bold as "YES")
REFRESH_INTERVAL = 5       # Seconds between data fetches from dump1090
```

**Common dump1090 aircraft.json paths (vary by software):**
- `/run/readsb/aircraft.json`
- `/run/adsbexchange-feed/aircraft.json`
- `/run/theairtraffic-feed/aircraft.json`
- `/run/adsblol-feed/aircraft.json`

---

## Code Architecture

The application is structured as a set of pure functions called from a single `main()` event loop.

### Function Reference

**`load_owner_data()` → `dict`** (lines 24-35)
- Reads `MASTER_CSV_PATH` using pandas
- Extracts columns `MODE S CODE HEX` and `NAME`
- Returns `{HEX_CODE: owner_name}` dictionary
- Hex codes are normalized to uppercase with whitespace stripped
- Called once at startup; result passed into `fetch_aircraft_data()`

**`haversine(lat1, lon1, lat2, lon2)` → `float`** (lines 38-44)
- Great-circle distance calculation between two coordinates
- Uses Earth radius of 6371 km, returns result in miles (multiplied by `KM_TO_MI`)
- Pure function, no side effects

**`get_direction(heading)` → `str`** (lines 47-53)
- Converts 0-360 degree bearing to a 16-point compass label (N, NNE, NE, ..., NNW)
- Uses `round(heading / 22.5) % 16` as index into a 17-element list

**`fetch_aircraft_data(owner_data)` → `list[dict]`** (lines 56-92)
- Fetches `DUMP1090_URL` with a 5-second timeout
- Filters aircraft to those with valid lat/lon and within `RADIUS_LIMIT`
- Looks up owner from `owner_data` dict; falls back to `"Unknown"`
- Ground speed (`gs`) is converted from knots to mph using `KM_TO_MI`
- Returns list of dicts with keys: `hex`, `flight`, `altitude`, `distance`, `distance_display`, `speed`, `owner`, `heading`, `direction`, `lat`, `lon`
- On any exception, logs to file and returns `[]`

**`display_header(stdscr, max_width)`** (lines 95-101)
- Renders the top 6 rows: border, app title, timestamp, border, column headers, border
- Column order: Flight | Hex | Altitude | Speed | Distance | Owner | Heading | Direction | Alert

**`display_footer(stdscr, row, max_width, seen_aircraft)`** (lines 104-106)
- Renders closing border and total unique aircraft count below the data rows

**`main(stdscr)`** (lines 109-181)
- Entry point called via `curses.wrapper(main)`
- Hides cursor, sets non-blocking input (`nodelay=True`)
- Loads owner data once, then enters infinite loop:
  1. Clears screen
  2. Gets terminal dimensions (`getmaxyx()`)
  3. If `REFRESH_INTERVAL` seconds have elapsed, re-fetches aircraft data
  4. Sorts aircraft by distance (closest first)
  5. Identifies proximity alerts (`distance <= PROXIMITY_ALERT`)
  6. Renders header, data rows (bold for alerts, truncated to terminal width), footer
  7. Refreshes display
  8. Checks for `q` keypress to exit
  9. Sleeps 100ms to avoid busy-waiting
- `curses.error` exceptions from out-of-bounds rendering are silently caught per row

### Data Flow

```
startup: load_owner_data() → owner_dict (held in memory)
loop:
  fetch_aircraft_data(owner_dict)
    → HTTP GET dump1090
    → filter by radius
    → enrich with owner lookup + distance + direction
    → return aircraft list
  sort by distance
  render with curses
```

---

## Running the Application

**Prerequisites:** Working dump1090 receiver accessible over HTTP, `master.csv` in place, configuration variables set.

```bash
python adsbtui.py
```

**Keyboard shortcuts:**
- `q` — quit

**Log file:** `adsb_tracker.log` is created/appended in the working directory. It captures all errors (CSV load failures, HTTP errors, etc.) at DEBUG level. The file is never rotated; it will grow unboundedly.

---

## FAA Master CSV Setup

The `MASTER.csv` file (included in this repo as an example) comes from the FAA Aircraft Registration Database. The file must have at minimum these columns:
- `MODE S CODE HEX` — the aircraft's ICAO Mode S hex code
- `NAME` — registered owner name

**Updating the database:**
1. Download from [FAA Aircraft Inquiry](https://registry.faa.gov/aircraftinquiry/)
2. Convert if needed:
   ```python
   import pandas as pd
   df = pd.read_csv("MASTER.txt", delimiter="|")
   df.to_csv("master.csv", index=False)
   ```

**Limitations of FAA data:**
- US-registered civil aircraft only
- Military aircraft excluded
- LADD (Limited Aircraft Display) and PIA (Privacy ICAO Address) aircraft excluded
- Non-US aircraft will show `"Unknown"` as owner

---

## Known Limitations

- No `requirements.txt` — dependencies must be installed manually
- No configuration file — users must edit `adsbtui.py` directly
- No automated tests
- Log file (`adsb_tracker.log`) grows indefinitely with no rotation
- Owner database is US-only and requires manual updates
- Aircraft type data unavailable (no free API/database exists for hex → type mapping)
- `ground speed (gs)` from dump1090 is in knots; the code multiplies by `KM_TO_MI` (0.621371) which is incorrect — knots × 0.621371 ≈ 0.716 mph; the correct factor is 1.15078. This is a bug.
- No Windows testing documented; `windows-curses` is mentioned but untested by the author

---

## Development Conventions

- **Single-file design:** All logic stays in `adsbtui.py`. Do not introduce a package structure unless absolutely necessary.
- **No build steps:** This is a plain Python script. Do not add build systems, packaging, or compilation.
- **Error handling:** All I/O operations (file reads, HTTP calls) use try/except and log to file. Curses rendering errors are caught silently per row to avoid crashing the UI.
- **No global state mutation:** `owner_data` and `seen_aircraft` are loaded/initialized in `main()` and passed or closed over. The module-level constants are never mutated at runtime.
- **String truncation for UI safety:** All fields displayed via curses are sliced to column widths before rendering. The full line is also sliced to `max_width - 1` before the `addstr` call.
- **Python version:** Target Python 3.6+ (f-strings used throughout; walrus operator and other newer features are not used).

---

## Git Workflow

- **Main branch:** `master`
- **Remote:** `http://local_proxy@127.0.0.1:36610/git/WPTK/ADSB-TUI`
- No `.gitignore` — all files including `MASTER.csv` and `adsb_tracker.log` are tracked
- No CI/CD pipeline
- Commit messages are short and imperative (e.g., "Create adsbtui.py", "Update README.md")
