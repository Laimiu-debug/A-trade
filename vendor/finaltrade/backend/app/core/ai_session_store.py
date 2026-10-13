"""Persist AI chat sessions under ~/.tdx-trend/ai_sessions/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from ..utils.persistence import read_state, write_state

DEFAULT_STATE_DIR = Path.home() / ".tdx-trend"
SESSION_DIR_NAME = "ai_sessions"
SESSION_TTL_DAYS = 30
MAX_MESSAGES_PER_SESSION = 50


def _session_dir() -> Path:
    path = DEFAULT_STATE_DIR / SESSION_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_path(session_id: str) -> Path:
    safe = "".join(ch for ch in session_id if ch.isalnum() or ch in ("_", "-"))
    return _session_dir() / f"{safe or uuid.uuid4().hex}.json"


def load_session(session_id: str) -> dict[str, Any] | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def save_session(
    session_id: str,
    *,
    messages: list[dict[str, str]],
    usage_total: dict[str, int] | None = None,
    page: str = "",
    title: str = "",
) -> None:
    trimmed = messages[-MAX_MESSAGES_PER_SESSION:]
    payload = {
        "session_id": session_id,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "page": page,
        "title": title,
        "messages": trimmed,
        "usage_total": usage_total or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "usage_scope": "session",
    }
    write_state(_session_path(session_id), payload)


def delete_session(session_id: str) -> bool:
    path = _session_path(session_id)
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return True


def list_sessions(limit: int = 20) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(_session_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        rows.append(
            {
                "session_id": str(data.get("session_id") or path.stem),
                "updated_at": str(data.get("updated_at") or ""),
                "page": str(data.get("page") or ""),
                "title": str(data.get("title") or ""),
                "message_count": len(data.get("messages") or []),
            }
        )
        if len(rows) >= limit:
            break
    return rows


def prune_expired_sessions() -> int:
    cutoff = datetime.now() - timedelta(days=SESSION_TTL_DAYS)
    removed = 0
    for path in _session_dir().glob("*.json"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            continue
        if mtime < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def load_usage_totals() -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    ledger = DEFAULT_STATE_DIR / 'ai_usage.json'
    if ledger.exists():
        data = read_state(ledger)
        return {key: max(0, int(data.get(key, 0) or 0)) for key in totals}
    legacy = dict(totals)
    for path in _session_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        usage = data.get("usage_total") if isinstance(data, dict) else None
        if not isinstance(usage, dict):
            continue
        for key in totals:
            value = max(0, int(usage.get(key, 0) or 0))
            if data.get('usage_scope') == 'session':
                totals[key] += value
            else:
                # Old files contain snapshots of one cumulative global counter.
                legacy[key] = max(legacy[key], value)
    return {key: totals[key] + legacy[key] for key in totals}


def save_usage_totals(totals: dict[str, int]) -> None:
    write_state(DEFAULT_STATE_DIR / 'ai_usage.json', totals)
