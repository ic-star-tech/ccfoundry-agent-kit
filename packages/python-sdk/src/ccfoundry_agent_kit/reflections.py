from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def read_reflections_config(reflections_dir: str | Path) -> dict[str, Any]:
    root = Path(reflections_dir)
    config_path = root / "config.yaml"
    if not config_path.exists():
        return {}
    try:
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def list_reflections(
    reflections_dir: str | Path,
    *,
    date: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    root = Path(reflections_dir)
    if not root.exists():
        return []

    entries: list[dict[str, Any]] = []
    if date:
        candidates = [root / f"{date}.jsonl"]
    else:
        candidates = sorted(root.glob("*.jsonl"), reverse=True)

    for path in candidates:
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                payload.setdefault("source_file", path.name)
                entries.append(payload)

    entries.sort(key=lambda item: str(item.get("ts") or ""), reverse=True)
    return entries[: max(0, limit)]
