"""Release gate: is the version in the source a valid next ZeroVer release?

Used by .github/workflows/release.yml, and unit-tested by tests/test_release_check.py.
Checks that `__version__` and pyproject.toml agree, that the version is ZeroVer
(0.MINOR.PATCH, optionally rcN), and that it sorts above every existing v* tag. Whether the
tag or release already exists on GitHub is checked by the workflow itself.

Prints `version=...`, `tag=...` and `prerelease=true|false` for $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

ZEROVER = re.compile(r"^0\.(0|[1-9]\d*)\.(0|[1-9]\d*)(rc([1-9]\d*))?$")


class ReleaseError(Exception):
    pass


def sort_key(version: str) -> tuple[int, int, int, int, int]:
    """Order versions as PEP 440 does: 0.10.0rc1 < 0.10.0rc2 < 0.10.0."""
    match = ZEROVER.match(version)
    if match is None:
        raise ReleaseError(
            f"{version!r} is not a ZeroVer version (0.MINOR.PATCH or 0.MINOR.PATCHrcN)"
        )
    minor, patch = int(match.group(1)), int(match.group(2))
    if match.group(4) is None:
        return (0, minor, patch, 1, 0)
    return (0, minor, patch, 0, int(match.group(4)))


def source_version(init_path: Path) -> str:
    found = re.search(r'^__version__ = "([^"]+)"', init_path.read_text("utf-8"), re.MULTILINE)
    if found is None:
        raise ReleaseError(f"no __version__ in {init_path}")
    return found.group(1)


def check(init_version: str, pyproject_version: str, existing_tags: list[str]) -> str:
    """Return the tag to create, or raise ReleaseError."""
    if init_version != pyproject_version:
        raise ReleaseError(
            f"__version__ {init_version!r} and pyproject.toml {pyproject_version!r} differ"
        )
    new = sort_key(init_version)
    tag = f"v{init_version}"
    for existing in existing_tags:
        name = existing.strip()
        if not name:
            continue
        if name == tag:
            raise ReleaseError(f"tag {tag} already exists; a published release is never replaced")
        if name.startswith("v") and ZEROVER.match(name[1:]) and sort_key(name[1:]) >= new:
            raise ReleaseError(f"{tag} does not sort above the existing tag {name}")
    return tag


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--init", type=Path, default=Path("src/adsbtui/__init__.py"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    parser.add_argument("--tags", default="", help="existing tags, one per line")
    args = parser.parse_args(argv)
    try:
        with open(args.pyproject, "rb") as handle:
            declared = tomllib.load(handle)["project"]["version"]
        version = source_version(args.init)
        tag = check(version, declared, args.tags.splitlines())
    except ReleaseError as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1
    print(f"version={version}")
    print(f"tag={tag}")
    print(f"prerelease={'true' if 'rc' in version else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
