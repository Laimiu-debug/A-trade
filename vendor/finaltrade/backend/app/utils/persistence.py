"""Atomic state writes that preserve the last readable version and report failures."""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any


class StatePersistenceError(RuntimeError):
    pass


class StateLoadError(StatePersistenceError):
    pass


def read_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise ValueError("expected an object")
        return value
    except (OSError, ValueError) as exc:
        raise StateLoadError(f"无法读取状态文件，原文件已保留：{path}") from exc


def write_state(path: Path, value: dict[str, Any]) -> None:
    temp_path: Path | None = None
    backup_temp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            # Never replace a valid backup with an unreadable state file.
            read_state(path)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.backup.", delete=False) as stream:
                backup_temp = Path(stream.name)
            shutil.copyfile(path, backup_temp)
            backup_temp.replace(path.with_name(path.name + ".bak"))
        temp_path.replace(path)
    except (OSError, ValueError, StatePersistenceError) as exc:
        raise StatePersistenceError(f"保存失败，已有状态文件已保留：{path}") from exc
    finally:
        for candidate in (temp_path, backup_temp):
            if candidate is not None:
                try:
                    candidate.unlink(missing_ok=True)
                except OSError:
                    # Cleanup must not hide the actual save failure.
                    pass
