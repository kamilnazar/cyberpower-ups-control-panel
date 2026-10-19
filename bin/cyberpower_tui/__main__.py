"""Allow ``python3 -m cyberpower_tui``."""

import sys

from .app import main

if __name__ == "__main__":
    sys.exit(main())
