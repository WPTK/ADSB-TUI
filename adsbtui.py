#!/usr/bin/env python3
"""Compatibility shim for the pre-1.0 single-file script.

The tool used to live entirely in this file. It is now a package under src/adsbtui/
with no third-party dependencies (the old version required pandas and requests). This
shim keeps `python3 adsbtui.py ...` working for anyone with that command in a systemd
unit, a cron job, or muscle memory.

The supported invocation is now:

    pip install -e .    # once
    adsbtui             # from anywhere

Configuration no longer lives in this file -- edit ~/.config/adsbtui/config.toml or pass
flags. See the README for the migration table from the old module-level constants.
"""

import os
import sys

# Put src/ ahead of this file's own directory on the path, so 'import adsbtui' resolves
# to the package rather than re-importing this shim.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)

try:
    from adsbtui.__main__ import main
except ImportError as exc:  # pragma: no cover - only hit on a broken checkout/install
    sys.exit(
        f"adsbtui: cannot load the package ({exc}).\n"
        "Run 'pip install -e .' from the repository root, then use the 'adsbtui' command."
    )

if __name__ == "__main__":
    sys.exit(main())
