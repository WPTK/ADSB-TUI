"""The real default alert channels: webhook bodies, the command's environment, desktop
notifications. test_dispatch.py covers the gate (cooldown, quiet hours) with fakes; this
file covers what the defaults actually send."""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from adsbtui import dispatch
from adsbtui.config import AlertsConfig
from adsbtui.model import Aircraft, AlertLevel


def _event(**kw) -> dispatch.AlertEvent:
    ac = Aircraft(
        hex="a1b2c3",
        is_icao=True,
        flight="UAL123",
        registration="N123UA",
        squawk="7700",
        distance_mi=2.345,
        bearing_deg=90.0,
        altitude_ft=12000.0,
        **kw,
    )
    return dispatch.AlertEvent("a1b2c3", AlertLevel.OVERHEAD, ac, "proximity")


class _Receiver:
    """A one-shot local HTTP server recording what is POSTed to it."""

    def __init__(self) -> None:
        self.bodies: list[tuple[str, bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                outer.bodies.append((self.headers.get("Content-Type", ""), self.rfile.read(length)))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def receiver():
    r = _Receiver()
    yield r
    r.close()


def _fire(cfg: AlertsConfig) -> None:
    dispatch.Dispatcher(cfg).webhook_fn(_event())


def test_json_webhook_posts_the_event_fields(receiver):
    _fire(AlertsConfig(webhook_url=receiver.url, webhook_format="json"))
    content_type, body = receiver.bodies[0]
    assert content_type == "application/json"
    payload = json.loads(body)
    assert payload["hex"] == "a1b2c3" and payload["event"] == "proximity"
    assert payload["distance_mi"] == pytest.approx(2.345)


def test_discord_webhook_posts_a_content_message(receiver):
    _fire(AlertsConfig(webhook_url=receiver.url, webhook_format="discord"))
    body = json.loads(receiver.bodies[0][1])
    assert body["content"].startswith("UAL123 [proximity/overhead] 2.3mi 12000ft")


def test_ntfy_webhook_posts_plain_text(receiver):
    _fire(AlertsConfig(webhook_url=receiver.url, webhook_format="ntfy"))
    content_type, body = receiver.bodies[0]
    assert content_type.startswith("text/plain")
    assert body.decode().startswith("UAL123 [proximity/overhead]")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://discord.com/api/webhooks/1/x", "discord"),
        ("https://discordapp.com/api/webhooks/1/x", "discord"),
        ("https://ntfy.sh/mytopic", "ntfy"),
        ("https://example.com/hook", "json"),
    ],
)
def test_auto_format_is_guessed_from_the_url(url, expected):
    assert dispatch._guess_webhook_format(url) == expected


def test_a_dead_webhook_is_swallowed():
    _fire(AlertsConfig(webhook_url="http://127.0.0.1:9/nothing-listens-here"))


def test_no_webhook_url_does_nothing():
    _fire(AlertsConfig(webhook_url=""))


def test_the_command_gets_the_event_in_its_environment(tmp_path):
    out = tmp_path / "env.txt"
    script = tmp_path / "rec.py"
    script.write_text(
        "import os, sys\n"
        "keys = ('HEX', 'REG', 'CALL', 'DIST', 'ALT')\n"
        "open(sys.argv[1], 'w').write(','.join(os.environ[k] for k in keys))\n",
        "utf-8",
    )
    cfg = AlertsConfig(command=f'"{sys.executable}" "{script}" "{out}"')
    dispatch.Dispatcher(cfg).command_fn(_event())
    assert out.read_text("utf-8") == "a1b2c3,N123UA,UAL123,2.35,12000"


def test_a_missing_command_is_swallowed():
    dispatch.Dispatcher(AlertsConfig(command="/no/such/binary --flag")).command_fn(_event())


def test_an_empty_command_does_nothing():
    dispatch.Dispatcher(AlertsConfig(command="")).command_fn(_event())


@pytest.mark.parametrize("system,program", [("Linux", "notify-send"), ("Darwin", "osascript")])
def test_desktop_notification_uses_the_platform_tool(monkeypatch, system, program):
    calls: list[list[str]] = []
    monkeypatch.setattr(dispatch.platform, "system", lambda: system)
    monkeypatch.setattr(dispatch.subprocess, "run", lambda argv, **kw: calls.append(argv) or None)
    dispatch._default_desktop_fn(_event())
    assert calls[0][0] == program
    assert "proximity" in " ".join(calls[0])


def test_a_missing_desktop_tool_is_swallowed(monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("notify-send")

    monkeypatch.setattr(dispatch.subprocess, "run", boom)
    dispatch._default_desktop_fn(_event())  # must not raise


def test_osascript_quoting_escapes_quotes_and_backslashes():
    assert dispatch._osascript_quote('a"b\\c') == '"a\\"b\\\\c"'


def test_the_event_message_omits_unknown_fields():
    ac = Aircraft(hex="a1b2c3", is_icao=True)
    text = dispatch._event_message(dispatch.AlertEvent("a1b2c3", AlertLevel.INBOUND, ac, "cpa"))
    assert "mi" not in text and "ft" not in text
