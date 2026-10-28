"""The version is declared twice (pyproject.toml, __init__); keep them equal and ZeroVer."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from adsbtui import __version__

ZEROVER = re.compile(r"^0\.(0|[1-9]\d*)\.(0|[1-9]\d*)(rc[1-9]\d*)?$")


def test_version_matches_pyproject():
    root = Path(__file__).resolve().parent.parent
    with open(root / "pyproject.toml", "rb") as fh:
        assert tomllib.load(fh)["project"]["version"] == __version__


def test_version_is_zerover():
    assert ZEROVER.match(__version__)
