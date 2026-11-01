"""Immutable runtime-file sharing and bounded cache cleanup; never user data."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

BUILD_KEY = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+-[0-9a-f]{20}")
DIGEST = re.compile(r"[0-9a-f]{64}")
GARBAGE = re.compile(r"\.gc-[0-9a-f]{32}")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_root(cache, runtime, staging=False):
    cache, runtime = Path(cache).absolute(), Path(runtime).absolute()
    if cache.is_symlink() or cache.is_junction() or runtime.is_symlink() or runtime.is_junction():
        raise ValueError("Runtime cache cannot be redirected")
    cache, runtime = cache.resolve(), runtime.resolve()
    name = runtime.name.removesuffix(".staging") if staging else runtime.name
    if runtime.parent != cache or not BUILD_KEY.fullmatch(name):
        raise ValueError("Runtime path is outside the managed cache")
    return cache, runtime


def prepare(cache, runtime):
    cache, runtime = checked_root(cache, runtime, staging=True)
    manifest = json.loads((runtime / "resources/cache-manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "atrade-cache-files-v1" or not manifest.get("files"):
        raise ValueError("Missing runtime content manifest")
    objects = cache / ".objects"
    if objects.is_symlink() or objects.is_junction():
        raise ValueError("Object store cannot be redirected")
    objects.mkdir(exist_ok=True)
    stats = {"verifiedFiles": 0, "sharedBytes": 0, "privateBytes": 0}
    verified_objects = set()
    for entry in manifest["files"]:
        relative = Path(entry["path"])
        expected = entry["sha256"]
        if relative.is_absolute() or ".." in relative.parts or not DIGEST.fullmatch(expected):
            raise ValueError("Invalid runtime content entry")
        source = runtime / relative
        if source.is_symlink() or not source.resolve().is_relative_to(runtime):
            raise ValueError("Runtime entry is outside the prepared directory")
        size = source.stat().st_size
        if size != entry["bytes"] or digest(source) != expected:
            raise ValueError("Prepared runtime failed content verification")
        stats["verifiedFiles"] += 1
        target = objects / expected
        temporary = source.with_name(source.name + ".link-" + uuid.uuid4().hex)
        try:
            if target.exists() and expected not in verified_objects:
                if target.is_symlink() or target.stat().st_size != size or digest(target) != expected:
                    # Unlink only this name; never overwrite an inode used by another version.
                    target.unlink()
            if not target.exists():
                os.link(source, target)
            elif not os.path.samefile(source, target):
                os.link(target, temporary)
                os.replace(temporary, source)
            verified_objects.add(expected)
            stats["sharedBytes"] += size
        except OSError:
            # FAT/exFAT or an in-use Python DLL can keep a verified private copy.
            stats["privateBytes"] += size
        finally:
            temporary.unlink(missing_ok=True)
    (runtime / ".atrade-cache-stats.json").write_text(json.dumps(stats), encoding="utf-8")
    return stats


def delete_lease(path):
    """A DELETE handle conflicts with the launcher's active directory lease."""
    if os.name != "nt":
        raise OSError("Cache collection requires Windows directory leases")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                  ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    kernel.CreateFileW.restype = ctypes.c_void_p
    handle = kernel.CreateFileW(str(path), 0x10000, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def collect(cache, current):
    # Called while the launcher holds the global cache mutex. Existing app
    # processes hold directory leases, and cannot be selected for removal.
    cache, current = checked_root(cache, current)
    removed, busy = [], []
    for candidate in cache.iterdir():
        if GARBAGE.fullmatch(candidate.name) and candidate.is_dir() and not candidate.is_symlink() and not candidate.is_junction():
            try:
                if candidate.resolve().parent != cache:
                    continue
                kernel, handle = delete_lease(candidate)
                try:
                    shutil.rmtree(candidate)
                finally:
                    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                    kernel.CloseHandle(handle)
            except OSError:
                busy.append(candidate.name)
            continue
        if candidate == current or not BUILD_KEY.fullmatch(candidate.name):
            continue
        try:
            _, candidate = checked_root(cache, candidate)
            if (candidate / ".atrade-runtime-ready").read_text(encoding="utf-8") != candidate.name:
                continue
            if not (candidate / "resources/app/portable-build.json").is_file():
                continue
            kernel, handle = delete_lease(candidate)
            try:
                destination = cache / (".gc-" + uuid.uuid4().hex)
                if destination.parent.resolve() != cache:
                    raise ValueError("Unexpected cleanup destination")
                candidate.rename(destination)
                shutil.rmtree(destination)
                removed.append(candidate.name)
            finally:
                kernel.CloseHandle.argtypes = [ctypes.c_void_p]
                kernel.CloseHandle(handle)
        except (OSError, ValueError):
            busy.append(candidate.name)
    objects = cache / ".objects"
    if objects.is_dir() and not objects.is_junction() and not objects.is_symlink():
        for obj in objects.iterdir():
            if DIGEST.fullmatch(obj.name) and not obj.is_symlink() and obj.stat().st_nlink == 1:
                try:
                    obj.unlink()
                except OSError:
                    pass
    return {"removedVersions": removed, "retainedVersions": busy}


if __name__ == "__main__":
    action, cache, runtime = sys.argv[1:]
    result = prepare(cache, runtime) if action == "prepare" else collect(cache, runtime) if action == "collect" else None
    if result is None:
        raise ValueError("Unsupported cache operation")
    if sys.stdout:
        print(json.dumps(result))
