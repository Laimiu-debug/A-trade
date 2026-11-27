"""Let a compatible Windows source checkout reuse its prepared release dependencies."""
import importlib.util
import json
from pathlib import Path
import platform
import site
import sys


def use_prepared_dependencies(*modules):
    root = Path(__file__).resolve().parents[1]
    if (root / "portable-build.json").is_file() or all(importlib.util.find_spec(name) for name in modules):
        return
    if sys.platform != "win32" or sys.implementation.name != "cpython" or platform.machine().lower() != "amd64":
        return
    dist = root / "dist"
    try:
        latest = json.loads((dist / "latest-portable.json").read_text(encoding="utf-8"))
        directory = Path(latest["directory"]).resolve()
        if directory.parent != dist:
            return
        build = json.loads((directory / "resources/app/portable-build.json").read_text(encoding="utf-8"))
        if build["pythonVersion"].split(".")[:2] != [str(sys.version_info.major), str(sys.version_info.minor)]:
            return
        packages = directory / "resources/python/Lib/site-packages"
        if packages.is_dir() and str(packages) not in sys.path:
            site.addsitedir(str(packages))
    except (OSError, ValueError, KeyError, TypeError):
        return
