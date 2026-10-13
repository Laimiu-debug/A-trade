"""Persist user AI preferences under ~/.tdx-trend/."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_STATE_DIR = Path.home() / ".tdx-trend"
LOCAL_PLAYBOOK_FILE = "ai_playbook.local.json"
QUICK_PROMPTS_FILE = "ai_quick_prompts.json"


def _state_dir() -> Path:
    return DEFAULT_STATE_DIR


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return data if isinstance(data, dict) else dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_local_playbook() -> dict[str, Any]:
    path = _state_dir() / LOCAL_PLAYBOOK_FILE
    data = _read_json(path, {"principles": [], "updated_at": ""})
    principles = data.get("principles")
    if not isinstance(principles, list):
        principles = []
    cleaned = [str(item).strip() for item in principles if str(item).strip()]
    return {
        "principles": cleaned,
        "updated_at": str(data.get("updated_at") or ""),
    }


def save_local_playbook(principles: list[str]) -> dict[str, Any]:
    cleaned = [str(item).strip() for item in principles if str(item).strip()]
    payload = {
        "principles": cleaned,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json(_state_dir() / LOCAL_PLAYBOOK_FILE, payload)
    return payload


def load_quick_prompts() -> dict[str, Any]:
    path = _state_dir() / QUICK_PROMPTS_FILE
    data = _read_json(path, {"templates": []})
    templates = data.get("templates")
    if not isinstance(templates, list):
        templates = []
    normalized: list[dict[str, Any]] = []
    for item in templates:
        if not isinstance(item, dict):
            continue
        page = str(item.get("page", "generic")).strip() or "generic"
        label = str(item.get("label", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not label or not prompt:
            continue
        normalized.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                "page": page,
                "label": label,
                "prompt": prompt,
                "pinned": bool(item.get("pinned", False)),
            }
        )
    return {"templates": normalized}


def save_quick_prompts(templates: list[dict[str, Any]]) -> dict[str, Any]:
    cleaned: list[dict[str, Any]] = []
    for item in templates:
        page = str(item.get("page", "generic")).strip() or "generic"
        label = str(item.get("label", "")).strip()
        prompt = str(item.get("prompt", "")).strip()
        if not label or not prompt:
            continue
        cleaned.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex[:12]),
                "page": page,
                "label": label,
                "prompt": prompt,
                "pinned": bool(item.get("pinned", False)),
            }
        )
    payload = {"templates": cleaned}
    _write_json(_state_dir() / QUICK_PROMPTS_FILE, payload)
    return payload
