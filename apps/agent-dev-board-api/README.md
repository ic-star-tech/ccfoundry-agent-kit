# Agent Dev Board API

This service manages local template-based agents from `.dev-board/`, keeps `agents.generated.yaml` in sync, and exposes the coordinator API behind `Agent Dev Board`.

It currently handles:

- local agent template listing
- local agent create / start / stop
- agent discovery and manifest fetch
- direct / inline chat proxying
- SSE chat streaming relay
- in-memory transcript state
- Foundry handshake probes
- local git and GitHub context discovery
- developer bootstrap ticket requests

The package and import path are named `agent-dev-board-api` / `agent_dev_board_api` to match the product surface in this repository.
