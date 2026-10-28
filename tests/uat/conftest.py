"""Fixtures for the UAT layer: an isolated home, a fake feed, and a terminal factory."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Windows has no pty. The nightly ConPTY smoke covers it separately.
if sys.platform == "win32":
    collect_ignore_glob = ["*.py", "flows/*.py"]
else:
    from fakefeed import HOME_LAT, HOME_LON, FakeFeed
    from harness import REPO_ROOT, Term

_HERE = Path(__file__).resolve().parent


def pytest_collection_modifyitems(config, items):
    for item in items:
        if _HERE in Path(str(item.fspath)).resolve().parents:
            item.add_marker(pytest.mark.uat)


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A throwaway HOME with XDG dirs; the child never touches the developer's own files."""
    for sub in ("home", "xdg-config", "xdg-data", "cwd"):
        (tmp_path / sub).mkdir()
    return tmp_path


@pytest.fixture
def feed():
    server = FakeFeed()
    yield server
    server.close()


def child_env(sandbox: Path, extra: dict[str, str | None] | None = None) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(sandbox / "home"),
        "XDG_CONFIG_HOME": str(sandbox / "xdg-config"),
        "XDG_DATA_HOME": str(sandbox / "xdg-data"),
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        # Tests are about the terminal, not the network: never download the registry.
        "ADSBTUI_REGISTRY_AUTO_UPDATE": "false",
        # Fast polling so a scripted fault shows up in well under a second.
        "ADSBTUI_SOURCE_REFRESH_S": "0.3",
        "ADSBTUI_SOURCE_TIMEOUT_S": "2",
    }
    # Subprocess coverage (see the uat CI job): the interpreter's .pth hook reads these.
    for passthrough in ("COVERAGE_PROCESS_START", "COVERAGE_FILE"):
        if passthrough in os.environ:
            env[passthrough] = os.environ[passthrough]
    for key, value in (extra or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


@pytest.fixture
def spawn(sandbox: Path, feed, request):
    """spawn(cols, rows, args=None, env=None, feed_args=True) -> a running Term.

    By default the child is pointed at the fake feed with a home position; pass
    feed_args=False to start with a bare command line (first-run and terminal tests).
    """
    started: list[Term] = []

    def _spawn(
        cols: int = 120,
        rows: int = 30,
        args: list[str] | None = None,
        env: dict[str, str | None] | None = None,
        feed_args: bool = True,
        wait_ready: bool = True,
    ) -> Term:
        argv = list(args or [])
        if feed_args:
            argv += ["--url", feed.url, "--lat", str(HOME_LAT), "--lon", str(HOME_LON)]
        term = Term(
            argv,
            cols=cols,
            rows=rows,
            env=child_env(sandbox, env),
            cwd=sandbox / "cwd",
            name=request.node.name.replace("/", "_")[:80],
        )
        started.append(term)
        if wait_ready:
            term.wait_for("FLIGHT")
        return term

    yield _spawn
    for term in started:
        term.close()
