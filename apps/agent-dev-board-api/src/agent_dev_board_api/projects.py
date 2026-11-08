"""Explicit operator-selected customer projects; inspection never imports code."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

MODULE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*$"
)
MANIFEST = "ccfoundry.project.json"


def configured_projects() -> list[Path]:
    try:
        value = json.loads(os.getenv("CCFOUNDRY_AGENT_PROJECTS", "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            "CCFOUNDRY_AGENT_PROJECTS must be a JSON array of project directories"
        ) from exc
    if not isinstance(value, list) or not all(
        isinstance(p, str) and p.strip() for p in value
    ):
        raise ValueError(
            "CCFOUNDRY_AGENT_PROJECTS must be a JSON array of project directories"
        )
    return [Path(p).expanduser().resolve(strict=True) for p in value]


def project_id(root: Path) -> str:
    return "project_" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:20]


def project_path(root: Path, value: str, *, directory: bool = False) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or Path(value).is_absolute()
        or ".." in Path(value).parts
    ):
        raise ValueError(
            "Project paths must be relative and remain inside the selected directory"
        )
    candidate = (root / value).resolve(strict=True)
    if not candidate.is_relative_to(root) or (directory and not candidate.is_dir()):
        raise ValueError("Project paths must remain inside the selected directory")
    return candidate


def load_project(root: Path) -> dict[str, Any]:
    if root.is_symlink():
        raise ValueError("A configured project directory was replaced by a symlink")
    root = root.resolve(strict=True)
    manifest = project_path(root, MANIFEST)
    if manifest.stat().st_size > 64 * 1024:
        raise ValueError("Project manifest exceeds 64 KiB")
    config = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or (
        type(config.get("version")) is not int or config["version"] != 1
    ):
        raise ValueError("Project manifest requires version 1")
    module = config.get("app_module", "")
    if not isinstance(module, str) or not MODULE.fullmatch(module):
        raise ValueError("Project app_module must be a Python module:ASGI_object")
    app_dir = project_path(root, config.get("app_dir", "src"), directory=True)
    module_path = app_dir.joinpath(*module.split(":")[0].split("."))
    candidates = [module_path.with_suffix(".py"), module_path / "__init__.py"]
    if not any(p.is_file() and p.resolve().is_relative_to(root) for p in candidates):
        raise ValueError("Project app_module must exist in its declared app_dir")
    space = project_path(root, config.get("agent_space", "agent_space"), directory=True)
    # A venv's python is normally a symlink to a system interpreter. The local
    # operator, through this manifest, selects that executable; the browser
    # cannot supply or replace an interpreter or source path.
    python = config.get("python", ".venv/bin/python")
    if not isinstance(python, str) or not python or "\x00" in python:
        raise ValueError("Project python must identify a prepared Python environment")
    python_path = Path(python).expanduser()
    if not python_path.is_absolute():
        python_path = root / python_path
    build = config.get("build", {})
    if not isinstance(build, dict):
        raise ValueError("Project build must be an object")
    return {
        "id": project_id(root),
        "kind": "project",
        "label": str(config.get("label") or root.name),
        "description": str(
            config.get("description") or "Customer Python agent project"
        ),
        "project_root": root,
        "app_dir": app_dir,
        "app_module": module,
        "agent_space_dir": space,
        "python": python_path,
        "build": build,
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
    }
