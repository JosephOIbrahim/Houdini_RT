"""Run all KarmaRT M0-M3 + Offscreen UI verification tests under real hython."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
PYTHON_DIR = str(ROOT / "python")
if PYTHON_DIR not in sys.path:
    sys.path.insert(0, PYTHON_DIR)

import hou


def main() -> int:
    print(f"=== KarmaRT QA Gate | Houdini {hou.applicationVersionString()} | Python {sys.version.split()[0]} ===")
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(ROOT / "tests"), pattern="test_*.py")
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print(
        f"SUMMARY: ran={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} "
        f"ok={result.wasSuccessful()}"
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
