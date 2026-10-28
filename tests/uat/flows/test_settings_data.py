"""U11 settings, U12 data and registry."""

from __future__ import annotations

import fakefeed
from keys import press


def test_settings_open_and_cancel(spawn):
    term = spawn()
    term.wait_for("UAL123")
    for opener in (",", "f2"):
        press(term, opener)
        term.wait_for("Source")
        press(term, "esc")
        term.wait_for("Refresh (s)", gone=True)


def test_changing_units_in_settings_applies_and_saves(spawn, sandbox):
    term = spawn()
    term.wait_for("5.3 mi")
    press(term, ",")
    term.wait_for("Refresh (s)")
    press(term, "tab", "tab", "tab")  # Source -> Home -> Filter -> Display
    term.wait_for("Units")
    press(term, "right")  # imperial -> next system
    press(term, "f2")  # apply and save
    term.wait_for("Refresh (s)", gone=True)
    term.wait_for("5.3 mi", gone=True)


def test_save_writes_only_the_file_layer_and_the_edit(spawn, sandbox):
    term = spawn()
    term.wait_for("UAL123")
    press(term, ",")
    term.wait_for("Refresh (s)")
    press(term, "f2")
    term.wait_for("Refresh (s)", gone=True)
    saved = next(sandbox.rglob("config.toml")).read_text("utf-8")
    assert "127.0.0.1" not in saved  # the --url came from the command line


def test_save_into_a_read_only_directory_shows_an_error(spawn, sandbox):
    config_dir = sandbox / "xdg-config" / "adsbtui"
    config_dir.mkdir(parents=True)
    config_dir.chmod(0o500)
    try:
        term = spawn()
        term.wait_for("UAL123")
        press(term, ",")
        term.wait_for("Refresh (s)")
        press(term, "f2")
        term.wait_for("could not save", timeout=5)
    finally:
        config_dir.chmod(0o700)


def test_data_screen_reports_an_empty_registry(spawn):
    term = spawn()
    term.wait_for("UAL123")
    for opener in ("D", "f8"):
        press(term, opener)
        term.wait_for("Aircraft registry sources")
        assert "not downloaded" in term.text
        press(term, "esc")
        term.wait_for("Aircraft registry sources", gone=True)


def test_manual_update_from_a_fake_registry(spawn):
    server = fakefeed.serve_registry()
    try:
        url = f"http://127.0.0.1:{server.server_port}/aircraft.csv.gz"
        term = spawn(env={"ADSBTUI_REGISTRY_TAR1090_URL": url})
        term.wait_for("UAL123")
        press(term, "D")
        term.wait_for("Aircraft registry sources")
        assert "not downloaded" in term.text
        press(term, "u")
        term.wait_for("tar1090-db 2 rows")
        press(term, "esc")
        term.wait_for("Aircraft registry sources", gone=True)
    finally:
        server.shutdown()
