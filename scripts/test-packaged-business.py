"""Run repository tests against the isolated, trimmed release Python runtime."""
from pathlib import Path
import sys
import unittest

import service

root = Path(__file__).resolve().parent.parent
expected = Path(sys.executable).resolve().parent.parent / "app/service"
if expected not in [Path(item).resolve() for item in service.__path__]:
    raise RuntimeError("This check requires the prepared release's embedded Python")
# Embedded Python deliberately ignores cwd/PYTHONPATH. Add only the repository
# test package, after the release app and dependencies, without shadowing them.
sys.path.append(str(root))
sys.path.append(str(root / "tests"))
suite = unittest.defaultTestLoader.discover(str(root / "tests"))
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
