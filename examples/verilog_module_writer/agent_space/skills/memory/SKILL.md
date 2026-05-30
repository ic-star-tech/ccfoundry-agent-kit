---
name: memory
description: View and manage durable user memory notes.
slash_command:
  cmd: /memory
  label: Memory
  desc: Save, review, or search durable memory notes.
---

# Memory Skill

## Trigger

Use this skill when the user asks to remember something, review long-term memory, or search durable notes.

## Data Sources

- `agent_space/notes.md`
- `agent_space/journal.md`

## Guidance

- Save new memory as short factual notes.
- Prefer append-only updates instead of rewriting older notes.
- When reviewing memory, surface the most recent relevant entries first.
- If the user asks to forget or rewrite stored memory, confirm before destructive edits.
