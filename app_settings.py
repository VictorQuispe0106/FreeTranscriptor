"""Tiny persisted settings for the app (shared by every front).

One JSON file (``transcriptor_config.json``, gitignored) with atomic writes:
write to a temp file in the same directory, then ``os.replace``. A missing or
corrupt file falls back to defaults instead of crashing on startup. Tests
point ``CONFIG_PATH`` at a temp file.
"""

from __future__ import annotations

import json
import os
import threading

# Same directory as the other project modules; no circular dependency:
# nothing here imports back into the core.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_PATH = os.path.join(PROJECT_DIR, "transcriptor_config.json")

_LOCK = threading.Lock()

_DEFAULTS: dict = {"auto_summary": False}


def load() -> dict:
    """Return the stored settings merged over the defaults."""
    data = dict(_DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            stored = json.load(fh)
    except (OSError, ValueError):
        return data  # missing or corrupt: defaults win
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in data:
                data[key] = value
    return data


def get_bool(key: str) -> bool:
    return bool(load().get(key))


def set_bool(key: str, value: bool) -> None:
    if key not in _DEFAULTS:
        raise KeyError(f"setting desconocido: {key}")
    with _LOCK:
        data = load()
        data[key] = bool(value)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
