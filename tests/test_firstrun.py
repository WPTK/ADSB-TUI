"""Tests for the first-run flow.

The point of these is the gate rather than the wizard itself: a person at a terminal
should be walked through setup, and everything else -- a cron job, a systemd unit, a
piped command -- must keep getting the plain error and a non-zero exit code instead of an
interactive UI it cannot answer.
"""

from __future__ import annotations

import argparse
import tomllib

import pytest

from adsbtui import firstrun
from adsbtui.config import Config


class _Stream:
    """Minimal stdin/stdout stand-in with a controllable isatty()."""

    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _args(**overrides) -> argparse.Namespace:
    base = dict(config=None, once=False, watch=None, headless=False, batch=False, check=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def _usable_config() -> Config:
    cfg = Config()
    cfg.source.url = "http://receiver.local/tar1090/data/aircraft.json"
    cfg.home.lat, cfg.home.lon = 37.7749, -122.4194
    return cfg


# ----------------------------------------------------------------------------------
# should_offer_setup
# ----------------------------------------------------------------------------------


def test_offers_setup_for_an_interactive_default_run():
    assert firstrun.should_offer_setup(_args(), stdin=_Stream(True), stdout=_Stream(True))


@pytest.mark.parametrize("flag", ["once", "headless", "batch", "check"])
def test_scripted_modes_never_get_a_wizard(flag):
    """These callers want output or an exit code. A UI would hang them."""
    args = _args(**{flag: True})
    assert not firstrun.should_offer_setup(args, stdin=_Stream(True), stdout=_Stream(True))


def test_watch_mode_never_gets_a_wizard():
    args = _args(watch=5)
    assert not firstrun.should_offer_setup(args, stdin=_Stream(True), stdout=_Stream(True))


@pytest.mark.parametrize("stdin_tty,stdout_tty", [(False, True), (True, False), (False, False)])
def test_non_interactive_streams_never_get_a_wizard(stdin_tty, stdout_tty):
    assert not firstrun.should_offer_setup(
        _args(), stdin=_Stream(stdin_tty), stdout=_Stream(stdout_tty)
    )


def test_an_explicit_config_path_is_an_error_to_report_not_a_blank_slate():
    """Naming a file that then fails to load is a mistake worth surfacing, not a reason
    to start over from nothing."""
    args = _args(config="/etc/adsbtui/config.toml")
    assert not firstrun.should_offer_setup(args, stdin=_Stream(True), stdout=_Stream(True))


def test_unparseable_arguments_do_not_offer_setup():
    assert not firstrun.should_offer_setup(None)


def test_a_closed_stream_is_treated_as_non_interactive():
    class Closed:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    assert not firstrun.should_offer_setup(_args(), stdin=Closed(), stdout=Closed())


# ----------------------------------------------------------------------------------
# first_run
# ----------------------------------------------------------------------------------


def test_completed_wizard_returns_a_config_and_saves_it(tmp_path, capsys):
    target = tmp_path / "config.toml"
    result = firstrun.first_run(
        _args(),
        setup_fn=_usable_config,
        save_fn=lambda cfg: firstrun.save_new_config(cfg, str(target)),
        stdin=_Stream(True),
        stdout=_Stream(True),
    )
    assert result is not None
    cfg, written = result
    assert cfg.source.url.endswith("aircraft.json")
    assert written == str(target)

    with open(target, "rb") as handle:
        assert tomllib.load(handle)["home"]["lat"] == pytest.approx(37.7749)


def test_cancelled_wizard_falls_through_to_the_error(capsys):
    assert (
        firstrun.first_run(
            _args(), setup_fn=lambda: None, stdin=_Stream(True), stdout=_Stream(True)
        )
        is None
    )


def test_a_wizard_config_that_does_not_validate_is_refused(capsys):
    """Better to report the original error than to run the session on something broken."""
    broken = Config()  # no source.url, placeholder home
    assert (
        firstrun.first_run(
            _args(), setup_fn=lambda: broken, stdin=_Stream(True), stdout=_Stream(True)
        )
        is None
    )
    assert "unusable" in capsys.readouterr().err


def test_a_config_that_cannot_be_saved_still_starts_the_session(capsys):
    result = firstrun.first_run(
        _args(),
        setup_fn=_usable_config,
        save_fn=lambda cfg: None,
        stdin=_Stream(True),
        stdout=_Stream(True),
    )
    assert result is not None
    assert "could not be saved" in capsys.readouterr().err


def test_scripted_run_skips_the_wizard_entirely():
    calls = []

    def setup_fn():
        calls.append(1)
        return _usable_config()

    assert (
        firstrun.first_run(
            _args(once=True), setup_fn=setup_fn, stdin=_Stream(True), stdout=_Stream(True)
        )
        is None
    )
    assert calls == []
