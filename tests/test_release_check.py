"""The release gate's version rules (see .github/scripts/release_check.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "release_check.py"
_SPEC = importlib.util.spec_from_file_location("release_check", _SCRIPT)
release_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(release_check)  # type: ignore[union-attr]

check = release_check.check
ReleaseError = release_check.ReleaseError


def test_the_first_release_needs_no_earlier_tag():
    assert check("0.10.0", "0.10.0", []) == "v0.10.0"


def test_a_new_release_must_sort_above_the_latest():
    assert check("0.10.1", "0.10.1", ["v0.9.0", "v0.10.0"]) == "v0.10.1"
    with pytest.raises(ReleaseError, match="does not sort above"):
        check("0.9.5", "0.9.5", ["v0.10.0"])


def test_an_existing_tag_is_never_reused():
    with pytest.raises(ReleaseError, match="already exists"):
        check("0.10.0", "0.10.0", ["v0.10.0"])


def test_a_release_candidate_sorts_below_its_final():
    assert check("0.10.0rc2", "0.10.0rc2", ["v0.10.0rc1"]) == "v0.10.0rc2"
    assert check("0.10.0", "0.10.0", ["v0.10.0rc2"]) == "v0.10.0"
    with pytest.raises(ReleaseError):
        check("0.10.0rc1", "0.10.0rc1", ["v0.10.0"])


def test_minor_sorts_numerically_not_as_text():
    assert check("0.10.0", "0.10.0", ["v0.9.0"]) == "v0.10.0"


@pytest.mark.parametrize("bad", ["1.0.0", "0.10", "0.10.0-rc1", "0.010.0", "0.10.0rc0", "v0.10.0"])
def test_only_zerover_versions_are_accepted(bad):
    with pytest.raises(ReleaseError, match="not a ZeroVer"):
        check(bad, bad, [])


def test_the_source_and_pyproject_versions_must_agree():
    with pytest.raises(ReleaseError, match="differ"):
        check("0.10.0", "0.9.0", [])


def test_foreign_tags_are_ignored():
    assert check("0.10.0", "0.10.0", ["nightly", "v1.2", "latest"]) == "v0.10.0"


def test_the_current_source_passes_the_gate_with_no_tags():
    root = Path(__file__).resolve().parent.parent
    assert (
        release_check.main(
            [
                "--init",
                str(root / "src/adsbtui/__init__.py"),
                "--pyproject",
                str(root / "pyproject.toml"),
            ]
        )
        == 0
    )
