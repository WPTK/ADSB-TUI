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
