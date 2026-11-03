# ADSB-TUI

A terminal UI that lists aircraft near you from the `aircraft.json` feed of your own `readsb` or `dump1090-fa` receiver. It renders a sortable table and a radar scope with curses.

![adsbtui showing live traffic around SFO: the aircraft table and the radar scope](docs/screenshots/main.png)

## Prerequisites

- Python 3.11+
- A `readsb` or `dump1090-fa` receiver whose `aircraft.json` you can reach by URL or file path
- Windows only: the `windows-curses` package (installed by the `windows` extra below)

## Quickstart

1. Clone and install:

   ```bash
   git clone https://github.com/WPTK/ADSB-TUI.git
   cd ADSB-TUI
   pip install -e .            # Windows: pip install -e ".[windows]"
   ```

   On Raspberry Pi OS, where plain `pip install` is blocked, use pipx:

   ```bash
   sudo apt install pipx && pipx ensurepath
   pipx install --editable .
   ```

2. Start the app:

   ```bash
   adsbtui
   ```

The first run starts a setup wizard that finds your receiver and home location and writes `~/.config/adsbtui/config.toml`. To skip the wizard, pass your receiver and location:

```bash
adsbtui --url 192.168.1.80 --lat 37.7749 --lon -122.4194
```

`--url` takes a receiver address (the usual `aircraft.json` paths are probed), an exact URL, or a local file such as `/run/readsb/aircraft.json`.

To update, run `git pull` and restart. A pipx install made without `--editable` needs `pipx install --force --editable .` once.

## Configuration

Precedence: flag, then `ADSBTUI_<SECTION>_<KEY>` environment variable, then `config.toml`, then default. Press `F2` in the app to edit every option.

| Variable / Flag | Default | Description |
|---|---|---|
| `--url`, `ADSBTUI_SOURCE_URL` | none (required) | Receiver address, URL or file path |
| `--lat`, `--lon`, `ADSBTUI_HOME_LAT`, `ADSBTUI_HOME_LON` | none (required) | Your location in decimal degrees |
| `--radius`, `ADSBTUI_FILTER_RADIUS` | `15.0` | Hide aircraft farther than this many miles |
| `--proximity`, `ADSBTUI_FILTER_PROXIMITY` | `5.0` | Miles within which an aircraft counts as overhead |
| `--refresh`, `ADSBTUI_SOURCE_REFRESH_S` | `5.0` | Seconds between polls |
| `--units`, `ADSBTUI_DISPLAY_UNITS` | `imperial` | `imperial`, `metric` or `aviation` |
| `--no-color` | off | Disable color |
| `--config`, `ADSBTUI_CONFIG` | `./adsb-tui.toml`, else `~/.config/adsbtui/config.toml` | Path to a TOML config file |
| `--check` | off | Validate the config, fetch once and exit |

The full key list, column meanings and watchlist format are in [docs/configuration.md](docs/configuration.md).

## Usage and controls

| Key | Action |
|---|---|
| `F1` / `?` | Help |
| `F2` / `,` | Settings |
| `F8` / `D` | Aircraft registry status and manual update |
| `Enter` / `d` | Details for the selected aircraft |
| `Up` / `Down` / `j` / `k` | Move the selection |
| `/` / `f` | Filter and search |
| `s` | Choose the sort column |
| `S` | Reverse the sort order |
| `c` | Choose columns |
| `w` | Watchlist |
| `r` | Toggle the radar scope |
| `u` | Cycle units |
| `p` | Pause |
| `+` / `-` | Grow or shrink the radius |
| `q` / `F10` | Quit |

![The detail pane for one aircraft, with closest approach and squawk](docs/screenshots/detail.png)

## License

[CC0 1.0](LICENSE). Downloaded registry data has its own terms, shown in the app: the FAA extract is public domain, and tar1090-db is for non-commercial use only.
