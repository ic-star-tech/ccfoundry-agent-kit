# Agent Dev Board API

This service manages local template-based agents from `.dev-board/`, keeps `agents.generated.yaml` in sync, and exposes the coordinator API behind `Agent Dev Board`.

It currently handles:

- local agent template listing
- local agent create / start / stop
- Skill Store browse / install / uninstall
- agent discovery and manifest fetch
- direct / inline chat proxying
- SSE chat streaming relay
- in-memory transcript state
- Foundry handshake probes
- local git and GitHub context discovery
- developer bootstrap ticket requests

The package and import path are named `agent-dev-board-api` / `agent_dev_board_api` to match the product surface in this repository.

## Skill Store Resource Files

`SkillStore.install_skill()` writes the installed skill into the selected agent instance under `agent_space/skills/<skill_id>/`. Built-in skills can declare resource source directories; the installer copies companion files alongside `SKILL.md` and `skill_meta.json`.

This matters for skills such as `ip_reference`, whose Verilog portfolio lives in a `references/` directory. The running agent resolves reference code relative to its own instance directory, so installing `ip_reference` must also install files like `references/rra/rra.v` and `references/rra/rra_tb.v`.
