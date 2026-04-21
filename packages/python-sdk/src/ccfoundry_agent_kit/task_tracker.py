from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

WEEKDAYS_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

_DATE_HEADER_RE = re.compile(r"^## 📅\s+(\d{4}-\d{2}-\d{2})")
_TASK_LINE_RE = re.compile(r"^- \[ \] (.+)")


def render_task_board_template(*, today: date | None = None) -> str:
    today = today or date.today()
    weekday = WEEKDAYS_ZH[today.weekday()]
    return (
        "# Personal Task Board\n\n"
        "- name: daily_summary\n"
        "  schedule_label: 每天 07:30\n"
        "  enabled: true\n"
        "  mode: execute\n"
        "  target: task\n"
        "  prompt: 总结昨天的聊天、记忆和任务进展，生成给人类看的每日总结。\n\n"
        f"## 📅 {today.isoformat()} ({weekday})\n\n"
        "### 待办\n\n"
        "### 进行中\n\n"
        "### 已完成\n\n"
        "### 备注\n\n"
        "---\n"
    )


@dataclass
class TaskTracker:
    task_file: Path | str

    def __post_init__(self) -> None:
        self.task_file = Path(self.task_file)

    def _read(self) -> str:
        if not self.task_file.exists():
            return ""
        return self.task_file.read_text(encoding="utf-8")

    def _write(self, content: str) -> None:
        self.task_file.parent.mkdir(parents=True, exist_ok=True)
        normalized = content if content.endswith("\n") else f"{content}\n"
        self.task_file.write_text(normalized, encoding="utf-8")

    def _today_header(self) -> str:
        today = date.today()
        weekday = WEEKDAYS_ZH[today.weekday()]
        return f"## 📅 {today.isoformat()} ({weekday})"

    def ensure_initialized(self) -> str:
        content = self._read()
        if not content.strip():
            content = render_task_board_template()
            self._write(content)
        return content

    def ensure_today_section(self) -> str:
        content = self.ensure_initialized()
        header = self._today_header()
        if header in content:
            return content

        block = (
            f"\n{header}\n\n"
            "### 待办\n\n"
            "### 进行中\n\n"
            "### 已完成\n\n"
            "### 备注\n\n"
            "---\n"
        )
        insert_at = content.find("\n## 📅")
        if insert_at == -1:
            content = content.rstrip() + block
        else:
            content = content[:insert_at] + block + content[insert_at:]
        self._write(content)
        return content

    def _find_section_anchor(self, content: str, section_name: str) -> int:
        today_header = self._today_header()
        today_pos = content.find(today_header)
        if today_pos == -1:
            return -1
        rest = content[today_pos:]
        section_pos = rest.find(f"### {section_name}")
        if section_pos == -1:
            return -1
        anchor = today_pos + section_pos + len(f"### {section_name}")
        while anchor < len(content) and content[anchor] == "\n":
            anchor += 1
            break
        return anchor

    def add_pending(self, description: str, *, priority: str = "normal") -> None:
        content = self.ensure_today_section()
        prefix = {"high": "🔴 ", "low": "🔵 "}.get(priority, "")
        entry = f"- [ ] {prefix}{description.strip()}\n"
        anchor = self._find_section_anchor(content, "待办")
        if anchor == -1:
            return
        content = content[:anchor] + entry + content[anchor:]
        self._write(content)

    def mark_in_progress(self, description: str) -> bool:
        content = self.ensure_today_section()
        for candidate in (f"- [ ] {description}", f"- [ ] 🔴 {description}", f"- [ ] 🔵 {description}"):
            pos = content.find(candidate)
            if pos == -1:
                continue
            content = content[:pos] + candidate.replace("- [ ]", "- [/]", 1) + content[pos + len(candidate) :]
            self._write(content)
            return True
        return False

    def mark_blocked(self, description: str, *, reason: str = "") -> bool:
        content = self.ensure_today_section()
        suffix = f" ({reason.strip()})" if reason.strip() else ""
        for candidate in (f"- [ ] {description}", f"- [/] {description}"):
            pos = content.find(candidate)
            if pos == -1:
                continue
            replacement = f"- [-] {description}{suffix}"
            content = content[:pos] + replacement + content[pos + len(candidate) :]
            self._write(content)
            return True
        return False

    def log_completion(self, description: str, *, timestamp: str = "") -> None:
        content = self.ensure_today_section()
        now = timestamp or datetime.now().strftime("%H:%M")
        entry = f"- [x] {now} - {description.strip()}\n"
        anchor = self._find_section_anchor(content, "已完成")
        if anchor == -1:
            return
        content = content[:anchor] + entry + content[anchor:]
        self._write(content)

    def log_heartbeat(self, message: str = "Heartbeat OK") -> None:
        content = self.ensure_today_section()
        now = datetime.now().strftime("%H:%M")
        entry = f"> {now} - {message.strip()}\n"
        anchor = self._find_section_anchor(content, "备注")
        if anchor == -1:
            return
        content = content[:anchor] + entry + content[anchor:]
        self._write(content)

    def get_pending_tasks(self) -> list[str]:
        content = self.ensure_today_section()
        today_header = self._today_header()
        today_pos = content.find(today_header)
        if today_pos == -1:
            return []
        rest = content[today_pos:]
        pending_pos = rest.find("### 待办")
        if pending_pos == -1:
            return []
        next_section = rest.find("### ", pending_pos + 1)
        pending_block = rest[pending_pos:] if next_section == -1 else rest[pending_pos:next_section]
        tasks: list[str] = []
        for line in pending_block.splitlines():
            match = _TASK_LINE_RE.match(line)
            if match:
                tasks.append(match.group(1).strip())
        return tasks

    def get_recurring_schedules(self) -> list[dict[str, Any]]:
        content = self.ensure_initialized()
        lines = content.splitlines()
        start_idx: int | None = None
        collected: list[str] = []
        for index, line in enumerate(lines):
            if _DATE_HEADER_RE.match(line):
                break
            if line.startswith("- name:"):
                start_idx = index
            if start_idx is not None:
                collected.append(line)
        if not collected:
            return []
        try:
            loaded = yaml.safe_load("\n".join(collected)) or []
        except Exception:
            return []
        return [item for item in loaded if isinstance(item, dict)]

    def update_recurring(self, task_name: str, *, status: str = "success") -> bool:
        content = self.ensure_initialized()
        lines = content.splitlines()
        last_run = datetime.now().isoformat(timespec="seconds")
        for index, line in enumerate(lines):
            if line.strip() != f"- name: {task_name}":
                continue
            insert_at = index + 1
            found_run = False
            found_status = False
            while insert_at < len(lines):
                current = lines[insert_at]
                if current and not current.startswith("  "):
                    break
                if current.startswith("  last_run:"):
                    lines[insert_at] = f"  last_run: {last_run}"
                    found_run = True
                elif current.startswith("  last_status:"):
                    lines[insert_at] = f"  last_status: {status}"
                    found_status = True
                insert_at += 1
            if not found_run:
                lines.insert(insert_at, f"  last_run: {last_run}")
                insert_at += 1
            if not found_status:
                lines.insert(insert_at, f"  last_status: {status}")
            self._write("\n".join(lines))
            return True
        return False
