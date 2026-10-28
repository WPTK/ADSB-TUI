# Versioning

adsbtui follows [ZeroVer](https://0ver.org): the version is `0.MINOR.PATCH` and the major
number stays 0. There will be no 1.0.

- Bump **MINOR** for new features and for any breaking change. The CHANGELOG says which.
- Bump **PATCH** for fixes only.
- A pre-release is written `0.10.0rc1` (PEP 440, so the wheel version matches the tag).
- A release tag is `v` plus the version, e.g. `v0.10.0`. A published release is never
  modified or replaced.

The version lives in `pyproject.toml` and `src/adsbtui/__init__.py`; a unit test keeps them
equal and in this format.

## Releasing

A release is cut by hand: bump the version in `pyproject.toml` and `src/adsbtui/__init__.py`
in a PR, merge it, then run the **Release** workflow from `main`. It checks the version is
ZeroVer and sorts above every existing `v*` tag, runs the tests, builds, confirms the wheel's
version equals the tag, and publishes a GitHub Release `v<version>` (marked a pre-release for
`rcN`). It stops if the tag or release already exists and never overwrites. Nothing is
uploaded to PyPI.
