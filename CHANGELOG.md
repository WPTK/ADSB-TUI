# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `AUDIT.md`: full audit findings table with status tracking (F01-F47).
- `tests/fixtures/sample_master.csv`: 50-row FAA registry sample for tests and docs (replaces
  committing the full 20 MB extract).
- `.gitignore` for bytecode caches, logs, local config, and the local registry copy.

### Changed
- `MASTER.csv` removed from version control (F30). The full FAA registry extract is no longer
  shipped in the repository; a small sample lives at `tests/fixtures/sample_master.csv`.

### Fixed
- README install/setup instructions that referenced the wrong script name, a placeholder clone
  URL, a nonexistent `curses` PyPI package, and ineffective `chmod`/`chown` steps (F12-F15).
