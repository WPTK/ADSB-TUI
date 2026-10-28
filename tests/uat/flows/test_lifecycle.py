"""U01/U02 first run, U15 quit, U16 sizes and resize, U17 terminals."""

from __future__ import annotations

import signal
import time

import fakefeed
import pytest
from fakefeed import ac
from harness import ALT_SCREEN_OFF
from keys import press

# ------------------------------------------------------------------------- U01 / U02


def _walk_the_wizard(term, feed):
    term.wait_for("Setup 1/4")
    press(term, feed.url, "f5")
    term.wait_for("responded")
    press(term, "enter")
    term.wait_for("Setup 2/4")
    press(term, "37.7749", "tab", "-122.4194", "enter")
    term.wait_for("Setup 3/4")
    press(term, "enter")
    term.wait_for("Setup 4/4")
    term.wait_for("Ready to write")
    press(term, "enter")


def test_wizard_writes_a_config_and_shows_the_table(spawn, feed, sandbox):
    term = spawn(100, 34, feed_args=False, wait_ready=False)
    _walk_the_wizard(term, feed)
    term.wait_for("UAL123")
    written = list(sandbox.rglob("config.toml"))
    assert len(written) == 1
    assert feed.url in written[0].read_text("utf-8")


def test_wizard_saves_where_the_loader_looks(spawn, feed, sandbox):
    term = spawn(100, 34, feed_args=False, wait_ready=False)
    _walk_the_wizard(term, feed)
    term.wait_for("UAL123")
    assert (sandbox / "xdg-config" / "adsbtui" / "config.toml").exists()


def test_ctrl_c_in_the_wizard_exits_cleanly(spawn):
    term = spawn(feed_args=False, wait_ready=False)
    term.wait_for("Setup 1/4")
    press(term, "ctrl-c")
    code = term.wait_exit()
    assert "Traceback" not in term.text
    assert code == 2  # cancelling leaves no config, so it reports the usual config error
    assert "Configuration error" in term.text
    assert ALT_SCREEN_OFF in bytes(term.raw)


def test_scripted_run_without_a_config_gets_an_error_not_a_wizard(spawn):
    term = spawn(feed_args=False, wait_ready=False, args=["--once"])
    assert term.wait_exit() == 2
    assert "Configuration error" in term.text
    assert "Setup" not in term.text


# ------------------------------------------------------------------------------ U15


@pytest.mark.parametrize("key", ["q", "Q", "f10"])
def test_quit_keys_exit_zero_and_restore_the_terminal(spawn, key):
    term = spawn()
    term.wait_for("UAL123")
    press(term, key)
    assert term.wait_exit() == 0
    assert ALT_SCREEN_OFF in bytes(term.raw)
    assert "Traceback" not in term.text


def test_ctrl_c_exits_without_a_traceback(spawn):
    term = spawn(80, 24)
    term.wait_for("UAL123")
    press(term, "ctrl-c")
    assert term.wait_exit() == 0
    assert "Traceback" not in term.text
    assert ALT_SCREEN_OFF in bytes(term.raw)


def test_sigterm_exits_and_restores_the_terminal(spawn):
    term = spawn()
    term.wait_for("UAL123")
    term.signal(signal.SIGTERM)
    term.wait_exit()
    assert ALT_SCREEN_OFF in bytes(term.raw)
    assert "Traceback" not in term.text


def test_quit_is_prompt_even_during_a_stalled_fetch(spawn, feed):
    feed.script(fakefeed.stall(120))
    term = spawn()
    feed.wait_requests(1)
    started = time.monotonic()
    press(term, "q")
    assert term.wait_exit(timeout=10) == 0
    assert time.monotonic() - started < 3.5


# ------------------------------------------------------------------------------ U16


@pytest.mark.parametrize("size", [(80, 24), (40, 12), (40, 10), (200, 60)])
def test_it_draws_at_every_size(spawn, size):
    term = spawn(*size)
    term.wait_for("UAL123")
    assert term.lines[0].startswith("LIVE")
    assert "Traceback" not in term.text


def test_too_small_terminal_says_so(spawn):
    term = spawn(20, 6, wait_ready=False)
    term.wait_for("Terminal too small")
    press(term, "q")
    assert term.wait_exit() == 0


def test_resize_through_extremes_recovers_the_header(spawn):
    term = spawn(80, 24)
    term.wait_for("FLIGHT")
    for cols, rows in ((50, 15), (120, 40), (12, 5), (80, 24)):
        term.resize(cols, rows)
        term.pump(0.3)
        if cols >= 40:
            term.wait_for("FLIGHT")
    term.wait_for("UAL123")
    assert term.lines[0].startswith("LIVE")
    press(term, "q")
    assert term.wait_exit() == 0


# ------------------------------------------------------------------------------ U17


def test_unset_term_is_refused_with_a_hint(spawn):
    term = spawn(wait_ready=False, env={"TERM": None})
    assert term.wait_exit() == 2
    assert "could not find terminal" in term.text


@pytest.mark.xfail(strict=True, reason="A18: TERM=dumb paints a blank screen and exits 0")
def test_dumb_terminal_is_refused(spawn):
    term = spawn(wait_ready=False, env={"TERM": "dumb"})
    assert term.wait_exit() == 2


def test_vt100_still_draws(spawn):
    term = spawn(env={"TERM": "vt100"})
    term.wait_for("UAL123")


def _coloured_cells(term) -> int:
    return sum(
        1
        for y in range(term.rows)
        for x in range(term.cols)
        if term.cell(x, y).fg not in ("default", "white") and term.cell(x, y).data.strip()
    )


def test_color_flag_never_draws_no_foreground_colour(spawn):
    term = spawn(args=["--no-color"])
    term.wait_for("UAL123")
    assert _coloured_cells(term) == 0


def test_colour_is_on_by_default(spawn):
    term = spawn()
    term.wait_for("UAL123")
    assert _coloured_cells(term) > 0


@pytest.mark.xfail(strict=True, reason="A19: NO_COLOR is ignored with display.color=auto")
def test_no_color_environment_variable(spawn):
    term = spawn(env={"NO_COLOR": "1"})
    term.wait_for("UAL123")
    assert _coloured_cells(term) == 0


def test_c_locale_with_a_non_ascii_callsign_does_not_crash(spawn, feed):
    feed.script(fakefeed.ok([ac("abc123", 37.78, -122.42, flight="日本語1", r="Ñandú")]))
    term = spawn(env={"LANG": "C", "LC_ALL": "C"})
    term.wait_for("FLIGHT")
    term.pump(1.0)
    assert "Traceback" not in term.text
    press(term, "q")
    assert term.wait_exit() == 0
