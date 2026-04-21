from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from .reflections import list_reflections, read_reflections_config
from .task_tracker import TaskTracker


@dataclass
class AgentSpace:
    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).expanduser().resolve()
        self.agent_dir = self.root / "agent_space"
        self.workspace_dir = self.root / "workspace"
        self.reflections_dir = self.agent_dir / "reflections"
        self.agent_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.reflections_dir.mkdir(parents=True, exist_ok=True)
        self.task_tracker = TaskTracker(self.agent_dir / "task.md")
        self.task_tracker.ensure_initialized()

    def resolve(self, relative_path: str) -> Path:
        return self.root / relative_path

    def agent_path(self, relative_path: str = "") -> Path:
        return self.agent_dir / relative_path

    def workspace_path(self, relative_path: str = "") -> Path:
        return self.workspace_dir / relative_path

    def read_text(self, relative_path: str, default: str = "") -> str:
        path = self.resolve(relative_path)
        if not path.exists():
            return default
        return path.read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def append_text(self, relative_path: str, content: str) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(content)

    def load_yaml(self, relative_path: str) -> dict[str, Any]:
        raw = self.read_text(relative_path, default="")
        if not raw.strip():
            return {}
        loaded = yaml.safe_load(raw) or {}
        return loaded if isinstance(loaded, dict) else {}

    def recent_notes(self, limit_chars: int = 2000) -> str:
        notes = self.agent_path("notes.md").read_text(encoding="utf-8") if self.agent_path("notes.md").exists() else ""
        if len(notes) <= limit_chars:
            return notes
        return notes[-limit_chars:]

    def append_note(self, note: str, source: str = "direct") -> None:
        note = str(note or "").strip()
        if not note:
            return
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        entry = f"\n- [{timestamp}] ({source}) {note}\n"
        self.append_text("agent_space/notes.md", entry)

    def reflections_config(self) -> dict[str, Any]:
        return read_reflections_config(self.reflections_dir)

    def list_reflections(self, *, date: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return list_reflections(self.reflections_dir, date=date, limit=limit)
