"""pytest bootstrap for the RT toggle extension.

Adds two directories to ``sys.path`` so tests run from a source checkout with
no installation:

* the extension root, so ``import rt_runtime`` resolves to
  ``extensions/rt_toggle/rt_runtime``
* the tests directory, so ``import fake_hou`` works

No Houdini, no hython, no Qt is imported here.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
EXT_ROOT = TESTS_DIR.parent

for _path in (str(TESTS_DIR), str(EXT_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
