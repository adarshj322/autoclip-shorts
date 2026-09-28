"""Dedup ledger for the auto-shorts VPS wrapper.

Shape: {"seen": {video_id: entry}}.
Writes are atomic (tmp file + os.replace).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def load_ledger(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"seen": {}}
    if not isinstance(data, dict):
        return {"seen": {}}
    seen = data.get("seen")
    if not isinstance(seen, dict):
        data["seen"] = {}
    return data


def save_ledger(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def is_seen(data: dict, video_id: str) -> bool:
    seen = data.get("seen", {})
    return video_id in seen


def mark_result(data: dict, video_id: str, entry: dict) -> dict:
    data.setdefault("seen", {})[video_id] = entry
    return data
