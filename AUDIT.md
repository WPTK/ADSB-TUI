# ADSB-TUI Audit Findings

Findings from a full multi-lens audit of the original single-file `adsbtui.py` (181 lines) and its
README, cross-checked against upstream readsb/dump1090 field documentation and the shipped
`MASTER.csv`. See the session plan for the full backlog and milestone mapping; this file tracks
disposition as implementation proceeds.

Verification column: **D** = verified directly (ran code, measured the file, fetched upstream docs);
**R** = verified by re-reading the code path; **M** = medium confidence, confirmed during
implementation. `Fix` names the backlog item (see plan) that closes it. `Status` is updated as
milestones land: `Open`, `Fixed (M#)`, or `Won't fix: reason`.

## Critical / High

| ID | Line | Finding | Ver | Fix | Status |
|---|---|---|---|---|---|
| F01 | 71 | `gs` is knots; multiplying by `KM_TO_MI` shows 54% of true mph (250 kt shows as 155 mph) | D | B01 | Fixed (M2) |
| F02 | 17 | `./master.csv` never matches shipped `MASTER.csv` on Linux; load fails silently, every owner is `Unknown` | D | B02, B03 | Fixed (M1) |
| F03 | 58 | Synchronous `requests.get` inside the render loop: `q` and resize ignored during fetch | R | B04 | Fixed (M3) |
| F04 | 95-106 | Header/footer `addstr` unguarded: `curses.error` exits the program on narrow/short terminals | D | B04, B06 | Fixed (M3) |
| F05 | 33, 90 | Every failure (bad URL, 404, timeout, missing CSV, bad JSON) is swallowed to the log silently | R | B05 | Fixed (M3) |
| F06 | 11 | Root logger at DEBUG opened at import time in CWD; unbounded urllib3 chatter | R | B08 | Fixed (M3) |
| F07 | 119 | `stdscr.clear()` every 100 ms forces a full repaint 10x/s | R | B04 | Fixed (M3) |
| F08 | 57-92 | One malformed record discards the entire fetch and blanks the table | D | B03 | Fixed (M1) |
| F09 | 14-16 | Config lives in committed source with unvalidated placeholders | R | B02, B08 | Fixed (M1) |
| F10 | 26 | `pandas` pulled in for a two-column dict; not installed in sandbox; heavy on a Pi | D | B03 | Fixed (M1) |
| F11 | 110 | `curses.curs_set(0)` unguarded: raises on terminals without cursor-visibility capability | R | B04 | Fixed (M3) |
| F12 | README 29 | `pip install pandas requests curses` fails: no `curses` project on PyPI | D | B33 | Fixed (M10) |
| F13 | README 64, 75 | README references `adsb_tracker.py`; the file is `adsbtui.py` | D | B33 | Fixed (M10) |
| F14 | README 51-62 | Wrong FAA link, wrong delimiter, bundled `MASTER.csv` never mentioned | D | B22, B33 | Fixed (M10) |
| F15 | README 80-121 | `chmod`/`chown` on `/run/readsb/aircraft.json` is a no-op | R | B21, B33 | Fixed (M10) |
| F16 | 16, README 69 | Documented URL path only works with a non-default web server alias | M | B21 | Partly fixed (M1): a bare path or file:// URL now works, so no web server is needed on the receiver. URL auto-probing is still deferred. |

## Medium

| ID | Line | Finding | Ver | Fix | Status |
|---|---|---|---|---|---|
| F17 | 63-70 | `seen_pos` ignored: stale positions used for distance/alert | D (docs) | B10 | Fixed (M2) |
| F18 | 98, 130 | "Last Update" shows wall clock; naive `datetime` interval can go negative on a clock step | R | B04, B05 | Fixed (M3) |
| F19 | 139 | Alert is a current-distance check, not the overflight prediction the README describes | R | B13 | Fixed (M6) |
| F20 | 76 | readsb enrichment fields (`r`, `t`, `desc`, `ownOp`, `year`, `dbFlags`) discarded | D (docs) | B11 | Fixed (M2) |
| F21 | 76 | `squawk` and `emergency` never surfaced | D (docs) | B12 | Fixed (M2) |
| F22 | 72 | `~`-prefixed non-ICAO addresses treated as ICAO | D (docs) | B01 | Fixed (M2) |
| F23 | 79, 82 | `alt_baro=="ground"` passes through as text; 0 ft/0 kt render `N/A` | D | B01 | Fixed (M2) |
| F24 | 66, 100 | "Heading"/"Direction" show ground track, not bearing from home | R | B13 | Fixed (M6) |
| F25 | 157 | Fixed 132-column row cut at terminal width; headline columns invisible under 132 cols | D | B06 | Fixed (M3) |
| F26 | 174-176 | `KEY_RESIZE` discarded; only lowercase `q` quits; Ctrl-C dumps a traceback | R | B04, B05 | Fixed (M3) |
| F27 | 146, 106 | Aircraft beyond visible rows silently dropped; footer count is lifetime, not shown live | R | B15, B05 | Fixed (M4): the table scrolls with a selectable cursor, and the status line count is live. |
| F28 | 56-92 | `fetch_aircraft_data` mixes I/O, parsing, enrichment, formatting; untestable; no tests/packaging | R | B09 | Fixed (M1) |
| F29 | 31 | NAME kept with 50-char padding; whitespace-only names render blank; long names truncated | D | B03, B06 | Fixed (M1) |
| F30 | repo | 20.7 MB `MASTER.csv` committed, undated, 3-column subset | D | B22 | Fixed (M0) |
| F31 | README 163-165 | "Non-US"/"type needs paid API"/"military-LADD-PIA" limitations are solvable for free | D (docs) | B22-B25 | Partly fixed (M2): the receiver's own enrichment fields are now read, covering non-US aircraft and type when the feed provides them. An automatic registry download is still deferred. |
| F32 | README 155 | Claims unique-aircraft logging; nothing but errors is logged | R | B29, B33 | Fixed (M9): each closed track is now summarised into a SQLite sightings table, and the README describes what is actually recorded. |
| F33 | 157-162 | Untrusted JSON strings reach `addstr` unfiltered: NUL raises uncaught `ValueError` | R | B06 | Fixed (M1) |
| F34 | 58 | No response size cap or wall-clock bound on the fetch | R | B03, B04 | Fixed (M1) |

## Low / Info

| ID | Line | Finding | Ver | Fix | Status |
|---|---|---|---|---|---|
| F35 | 115 | First fetch waits a full refresh interval; empty table at launch | R | B04 | Fixed (M3) |
| F36 | 138, 155 | Per-frame sort/format and O(n*m) alert membership check | R | B04 | Fixed (M3) |
| F37 | 111, 178 | `nodelay`+`sleep` instead of `stdscr.timeout()` | R | B04 | Fixed (M3) |
| F38 | 135 | Poll period drifts because timestamp is taken after the fetch | R | B04 | Fixed (M3) |
| F39 | 113 | CSV loads after curses init with a blank screen | D | B03 | Fixed (M3) |
| F40 | 52 | `get_direction`: banker's rounding at exact boundaries; unreachable 17th entry | D | B01 | Fixed (M2) |
| F41 | 70, 84 | `0 < distance` excludes exact-overhead aircraft; `.0f` renders 359.5 as 360 | D | B01 | Fixed (M2) |
| F42 | 98 | "Last Update" line is 2 columns short, border misaligned | D | B06 | Fixed (M3) |
| F43 | 152 | A user-converted CSV with truly empty cells would crash `owner[:30]` via pandas NaN | D | B03 | Won't fix as stated: pandas removed entirely (B03); not reachable in the new code |
| F44 | 134 | `seen_aircraft` grows for the life of the process | R | B29 | Partly fixed (M3): tracks are now aged out of memory via the linger window rather than accumulating forever. |
| F45 | README | No H1 title, placeholder URL, stray bullet, typos, overstated license text, no config/troubleshooting sections | D | B33 | Fixed (M10) |
| F46 | repo | No version, tags, or changelog | D | B09 | Fixed (M0) |
| F47 | 58 | New TCP connection per poll (no keep-alive) | R | B03 | Fixed (M1) |

Refuted or downgraded during review: a NaN-owner crash does not occur with the shipped file;
`get_direction` is otherwise correct; no duplicate or malformed hex codes exist in `MASTER.csv`.
