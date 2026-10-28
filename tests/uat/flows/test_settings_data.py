"""U11 settings, U12 data and registry."""

from __future__ import annotations

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


def _serve_registry(tmp_path):
    """A local tar1090-db: two aircraft, gzipped, served over HTTP."""
    import gzip
    import http.server
    import threading

    body = gzip.compress(
        b"a1b2c3;N123UA;B738;00;BOEING 737-800;2010;UNITED AIR LINES;\n"
        b"abc123;N456XY;C172;00;CESSNA 172;1980;SOME OWNER;\n"
    )

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_manual_update_from_a_fake_registry(spawn, tmp_path):
    server = _serve_registry(tmp_path)
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
