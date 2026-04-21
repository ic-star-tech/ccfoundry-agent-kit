---
name: summary
description: Summarize recent work, memory, and task progress.
slash_command:
  cmd: /summary
  label: Summary
  desc: Summarize recent notes, tasks, and conversation context.
---

# Summary Skill

## Trigger

Use this skill when the user asks for a recap, status update, or work summary.

## Data Sources

- `agent_space/journal.md`
- `agent_space/notes.md`
- `agent_space/task.md`
- `agent_space/todo.md`

## Guidance

- Base the summary on recorded files instead of guessing.
- Separate completed work, in-progress work, and next steps.
- If there is little recorded context, say that clearly and keep the summary short.
