from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


def _parse_frontmatter(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}
    end = content.find("\n---", 3)
    if end == -1:
        return {}
    try:
        loaded = yaml.safe_load(content[3:end]) or {}
    except Exception:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def iter_skill_files(skills_dir: str | Path) -> list[tuple[str, Path]]:
    root = Path(skills_dir)
    if not root.exists():
        return []

    skills: list[tuple[str, Path]] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        if skill_md.is_file():
            skills.append((skill_md.parent.name, skill_md))
    for legacy_md in sorted(root.glob("*.md")):
        if legacy_md.is_file() and not legacy_md.name.startswith("."):
            skills.append((legacy_md.stem, legacy_md))
    return skills


def compute_skills_hash(skills_dir: str | Path) -> str:
    hasher = hashlib.md5()
    found = False
    for _, path in iter_skill_files(skills_dir):
        hasher.update(path.read_bytes())
        found = True
    return hasher.hexdigest() if found else ""


def scan_loaded_skills(skills_dir: str | Path) -> list[str]:
    seen: dict[str, None] = {}
    for skill_ref, _ in iter_skill_files(skills_dir):
        seen.setdefault(skill_ref, None)
    return list(seen.keys())


def scan_slash_commands(skills_dir: str | Path) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for skill_ref, path in iter_skill_files(skills_dir):
        frontmatter = _parse_frontmatter(path)
        slash_command = frontmatter.get("slash_command")
        if isinstance(slash_command, dict) and str(slash_command.get("cmd") or "").strip():
            commands.append(
                {
                    "cmd": str(slash_command.get("cmd")).strip(),
                    "label": str(slash_command.get("label") or skill_ref.replace("_", " ").title()).strip(),
                    "desc": str(slash_command.get("desc") or frontmatter.get("description") or "").strip(),
                    "skill_ref": skill_ref,
                }
            )
            continue
        commands.append(
            {
                "cmd": f"/{skill_ref}",
                "label": skill_ref.replace("_", " ").title(),
                "desc": str(frontmatter.get("description") or f"Use the {skill_ref} skill.").strip(),
                "skill_ref": skill_ref,
            }
        )
    return commands
