# Built-in Skills

This directory contains the official **CCFoundry Agent Kit** skills that ship with the SDK. Each skill follows the standard SKILL.md format and can be:

1. **Copied manually** into your agent's `agent_space/skills/` directory
2. **Installed via the Skill Store** in the Dev Board UI
3. **Used as reference** for creating your own custom skills

## Skill Format

Each skill is a subdirectory containing:

```
skills/
  my_skill/
    SKILL.md          # Required: YAML frontmatter + instructions
    skill_meta.json   # Optional: version, author metadata
    scripts/          # Optional: helper scripts
    examples/         # Optional: example files
```

### SKILL.md Structure

```markdown
---
name: my_skill
description: What this skill does.
slash_command:
  cmd: /my_skill
  label: My Skill
  desc: User-facing description for the slash command menu.
---

# My Skill

## Trigger
When to activate this skill.

## Capabilities
What the skill enables the agent to do.

## Implementation Guide
Step-by-step instructions for the agent.
```

## Available Skills

| Skill | Category | Description |
|-------|----------|-------------|
| [verilog_rra](./verilog_rra/) | hardware | Build & verify Round Robin Arbiter testbenches with iverilog |
| [sandbox_exec](./sandbox_exec/) | infrastructure | Execute commands & manage files in a Foundry sandbox |
| [foundry_settlement](./foundry_settlement/) | payment | Handle task settlement & Stripe payments |
| [memory](./memory/) | core | Durable memory notes & journal management |
| [summary](./summary/) | core | Work progress summaries & status reports |

## Creating Custom Skills

1. Create a new directory under your agent's `agent_space/skills/`
2. Add a `SKILL.md` with YAML frontmatter (see format above)
3. The SDK will auto-detect and register the skill
4. If you include a `slash_command` in frontmatter, it becomes a slash command

See the [SDK skills module](../src/ccfoundry_agent_kit/skills.py) for the scanning logic.
