"""Weekly check against the real registry upstreams (never run on a PR).

Downloads both sources with the program's own User-Agent and builds them into a scratch
database, so a changed URL, a blocked User-Agent or a changed file format shows up here
before it shows up for a user. Exit 0 = healthy. Not collected by pytest.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from adsbtui.config import RegistryConfig
from adsbtui.enrich import registry_update

#: Both real sources hold several hundred thousand aircraft; far below this means a
#: truncated or restructured file.
MIN_ROWS = 100_000


def main() -> int:
    cfg = RegistryConfig()
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "registry.sqlite")
        for name, build, url in (
            ("tar1090", registry_update.build_from_tar1090, cfg.tar1090_url),
            ("faa", registry_update.build_from_faa_zip, cfg.faa_url),
        ):
            try:
                result = build(url, db, force=True)
            except registry_update.RegistryError as exc:
                failures.append(f"{name}: {exc}")
                continue
            print(f"{name}: {result.row_count} rows")
            if result.row_count < MIN_ROWS:
                failures.append(f"{name}: only {result.row_count} rows (< {MIN_ROWS})")
    for line in failures:
        print("FAIL", line)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
