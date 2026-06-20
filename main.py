from __future__ import annotations

import os
import sys


ADDON_ROOT = os.path.dirname(os.path.abspath(__file__))
if ADDON_ROOT not in sys.path:
    sys.path.insert(0, ADDON_ROOT)

from resources.lib.app import run


if __name__ == "__main__":
    run()