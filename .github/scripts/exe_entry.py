"""PyInstaller entry point for the Windows release exe: runs the same main() as `adsbtui`."""

import sys

from adsbtui.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
