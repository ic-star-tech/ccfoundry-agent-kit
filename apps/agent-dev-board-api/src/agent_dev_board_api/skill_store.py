"""Skill Store — browse, install, and manage agent skills.

Provides a curated registry of skills that can be installed into local agents.
Each skill is a directory with a SKILL.md (YAML frontmatter + instructions)
and optionally scripts/resources.
"""
from __future__ import annotations

import json
import shutil
import textwrap
from pathlib import Path
from typing import Any


_REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------------------
# Built-in skill catalogue
# ---------------------------------------------------------------------------

_BUILTIN_SKILLS: list[dict[str, Any]] = [
    {
        "id": "verilog_rra",
        "name": "Verilog RRA Testbench",
        "category": "hardware",
        "tags": ["verilog", "RRA", "iverilog", "testbench", "hardware"],
        "description": "Build and verify Round Robin Arbiter (RRA) testbenches using Icarus Verilog in a Foundry sandbox.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
            ---
            name: verilog_rra
            description: Build and verify Round Robin Arbiter testbenches using Icarus Verilog.
            slash_command:
              cmd: /verilog_rra
              label: Verilog RRA
              desc: Write, compile, and simulate a Round Robin Arbiter testbench with iverilog.
            ---

            # Verilog RRA Testbench Skill

            ## Trigger
            Use this skill when the user asks to build, verify, or test a Round Robin
            Arbiter (RRA) in Verilog, or when a Foundry requirement mentions RRA/verilog.

            ## Capabilities
            - Write a parametric Round Robin Arbiter module (Verilog-2001)
            - Generate a self-checking testbench with pass/fail assertions
            - Compile using `iverilog` and simulate using `vvp`
            - Parse simulation output to determine pass/fail

            ## Implementation Guide

            ### RRA Module (rra.v)
            Use a mask-based approach for fair round-robin scheduling:
            - Maintain a 4-bit mask register
            - After granting bit[i], set mask to clear bits <= i
            - Use lowest-set-bit extraction: `x & (~x + 1)`
            - When masked_req is empty, wrap around to unmasked req

            ### Testbench (rra_tb.v)
            - Use `$dumpfile` / `$dumpvars` for waveform capture
            - Include reset sequence (rst_n=0 for 20ns, then rst_n=1)
            - Test single requests, round-robin all-request, and no-request cases
            - Print PASS/FAIL for each test case
            - Print summary with `ALL_TESTS_PASSED` or `SOME_TESTS_FAILED`

            ### Sandbox Commands
            ```bash
            # Compile
            iverilog -o rra_sim rra.v rra_tb.v

            # Simulate
            vvp rra_sim

            # View waveforms (if GTKWave available)
            gtkwave rra_tb.vcd
            ```

            ## Requirements
            - Foundry sandbox with `iverilog` and `vvp` installed
            - Workspace write access via sandbox API

            ## Success Criteria
            - All testbench assertions pass
            - `ALL_TESTS_PASSED` appears in simulation output
        """),
    },
    {
        "id": "sandbox_exec",
        "name": "Sandbox Executor",
        "category": "infrastructure",
        "tags": ["sandbox", "docker", "execution", "workspace"],
        "description": "Execute commands and manage files in a Foundry sandbox workspace.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
            ---
            name: sandbox_exec
            description: Execute commands and manage files in a Foundry sandbox.
            slash_command:
              cmd: /sandbox
              label: Sandbox
              desc: Run commands, write files, and manage a Foundry sandbox workspace.
            ---

            # Sandbox Executor Skill

            ## Trigger
            Use this skill when the task requires executing commands in an isolated
            sandbox environment, or when managing workspace files.

            ## API Reference

            ### Acquire a sandbox lease
            ```
            POST {SANDBOX_DAEMON_URL}/acquire
            Body: {"username": "<agent_username>"}
            Header: x-sandbox-token: <token>
            ```

            ### Execute a command
            ```
            POST {SANDBOX_DAEMON_URL}/leases/{lease_id}/terminal/exec
            Body: {"command": "<shell_command>"}
            ```

            ### Write a file
            ```
            PUT {SANDBOX_DAEMON_URL}/users/{username}/workspace/write
            Body: {"path": "<relative_path>", "content": "<file_content>"}
            ```

            ### Release the sandbox
            ```
            POST {SANDBOX_DAEMON_URL}/release
            Body: {"lease_id": "<lease_id>"}
            ```

            ## Best Practices
            - Always release the sandbox lease after use
            - Wait 3-5 seconds after sandbox acquisition for initialization
            - Use unique markers in commands to detect success (e.g., `echo EXIT_CODE=$?`)
            - Set reasonable timeouts for long-running commands
        """),
    },
    {
        "id": "foundry_settlement",
        "name": "Foundry Settlement",
        "category": "payment",
        "tags": ["settlement", "payment", "stripe", "bounty"],
        "description": "Handle task settlement and Stripe payments for completed bounty work.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
            ---
            name: foundry_settlement
            description: Handle Foundry task settlement and Stripe payments.
            slash_command:
              cmd: /settle
              label: Settlement
              desc: Trigger settlement for completed bounty work via Foundry and Stripe.
            ---

            # Foundry Settlement Skill

            ## Trigger
            Use when work is completed and payment needs to be triggered, or when
            querying settlement history.

            ## Settlement Flow
            1. Ops Agent verifies task completion
            2. Admin triggers `POST /api/agents/{agent_name}/settle`
            3. Foundry creates a Settlement Mandate
            4. Stripe PaymentIntent is created (if configured)
            5. Agent receives settlement notification via bootstrap

            ## API
            ```
            POST {FOUNDRY_URL}/api/agents/{agent_name}/settle
            Body: {
              "task_ref": "bounty:<requirement_id>",
              "amount": 0.75,
              "currency": "USD"
            }
            ```

            ## Mandate Chain
            IntentMandate (Match Policy) → CartMandate (Agent Bid) → SettlementMandate (Payment)
        """),
    },
    {
        "id": "memory",
        "name": "Memory Manager",
        "category": "core",
        "tags": ["memory", "notes", "journal", "persistence"],
        "description": "View and manage durable user memory notes and journal entries.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
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
            Use this skill when the user asks to remember something, review long-term
            memory, or search durable notes.

            ## Data Sources
            - `agent_space/notes.md`
            - `agent_space/journal.md`

            ## Guidance
            - Save new memory as short factual notes.
            - Prefer append-only updates instead of rewriting older notes.
            - When reviewing memory, surface the most recent relevant entries first.
            - If the user asks to forget or rewrite stored memory, confirm before destructive edits.
        """),
    },
    {
        "id": "summary",
        "name": "Work Summary",
        "category": "core",
        "tags": ["summary", "recap", "status", "report"],
        "description": "Summarize recent work, memory, and task progress.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
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
        """),
    },
    {
        "id": "coding_style",
        "name": "Verilog Coding Style",
        "category": "hardware",
        "tags": ["verilog", "coding-style", "rtl", "best-practices", "lint"],
        "description": "Verilog-2001 coding style guidelines and best practices for synthesizable RTL design.",
        "author": "ccfoundry",
        "version": "1.0.0",
        "skill_md": textwrap.dedent("""\
            ---
            name: coding_style
            description: Verilog-2001 coding style guidelines and best practices for synthesizable RTL.
            slash_command:
              cmd: /style
              label: Coding Style
              desc: Review Verilog coding style guidelines and best practices.
            ---

            # Verilog Coding Style Guide

            ## Trigger
            Use this skill when writing or reviewing Verilog code to ensure consistency,
            readability, and synthesizability.

            ## General Rules

            ### File Organization
            - One module per file
            - Filename matches module name: `rra.v` contains `module rra`
            - Testbench files use `_tb` suffix: `rra_tb.v`

            ### Naming Conventions
            | Element | Convention | Example |
            |---------|-----------|---------|
            | Modules | lowercase_snake | `round_robin_arbiter` |
            | Signals | lowercase_snake | `data_valid`, `read_enable` |
            | Parameters | UPPER_SNAKE | `DATA_WIDTH`, `ADDR_DEPTH` |
            | Active-low | `_n` suffix | `rst_n`, `cs_n` |
            | Clock | `clk` prefix | `clk`, `clk_div2` |

            ### Reset Strategy
            - Always use async active-low reset (`negedge rst_n`)
            - Reset all registers to known values

            ### Coding Rules
            - Use `always @(*)` for combinational, `always @(posedge clk or negedge rst_n)` for sequential
            - Blocking (`=`) in combinational, non-blocking (`<=`) in sequential
            - Always include `default` in `case` statements
            - One module per file, ANSI-style port declarations

            ### Things to Avoid
            - SystemVerilog features (`logic`, `always_ff`)
            - Variable declarations in unnamed blocks
            - `initial` blocks in synthesizable code
            - Mixing blocking/non-blocking in one always block
        """),
    },
    {
        "id": "ip_reference",
        "name": "IP Reference Portfolio",
        "category": "hardware",
        "tags": ["verilog", "IP", "RTL", "FIFO", "RRA", "counter", "CDC", "portfolio"],
        "description": "Browse the IP portfolio with reference implementations of common digital modules (RRA, FIFO, edge detector, priority encoder, etc.).",
        "author": "ccfoundry",
        "version": "1.0.0",
        "resource_source_dir": _REPO_ROOT / "examples" / "verilog_module_writer" / "agent_space" / "skills" / "ip_reference",
        "skill_md": textwrap.dedent("""\
            ---
            name: ip_reference
            description: Browse the IP portfolio with reference implementations of common digital modules.
            slash_command:
              cmd: /ip
              label: IP Reference
              desc: Browse the IP portfolio and reference implementations (RRA, FIFO, etc.).
            ---

            # IP Reference Portfolio

            ## Trigger
            Use this skill when implementing standard digital modules. Reference these
            proven implementations instead of writing from scratch.

            ## Available Modules

            | Module | Category | Description |
            |--------|----------|-------------|
            | rra | Arbitration | Mask-based 4-bit Round Robin Arbiter |
            | sync_fifo | Memory | Synchronous FIFO with full/empty flags |
            | edge_detect | Utility | Rising/falling/both edge detector |
            | priority_enc | Logic | Parameterized priority encoder |
            | pulse_sync | CDC | Single-pulse clock domain crossing |
            | counter | Utility | Up/down counter with overflow |

            ## Usage
            When implementing a Foundry bounty that involves any of these modules:
            1. Reference this portfolio for the standard implementation
            2. Adapt parameters as needed by the bounty spec
            3. Generate a testbench covering edge cases
            4. Compile and simulate in the Foundry sandbox
            5. Verify ALL_TESTS_PASSED before submission

            See the full SKILL.md in agent_space/skills/ip_reference/ for complete
            Verilog source code of each module.
        """),
    },
]


class SkillStore:
    """Manages the skill catalogue and per-agent skill installation."""

    def __init__(self, *, extra_skills: list[dict[str, Any]] | None = None) -> None:
        self._catalogue: dict[str, dict[str, Any]] = {}
        for skill in _BUILTIN_SKILLS:
            self._catalogue[skill["id"]] = skill
        for skill in (extra_skills or []):
            self._catalogue[skill["id"]] = skill

    # ── Catalogue ──────────────────────────────────────────────────────

    def list_store(self, *, category: str = "", tag: str = "") -> list[dict[str, Any]]:
        """Return all skills in the store (optionally filtered)."""
        results = []
        for skill in self._catalogue.values():
            if category and skill.get("category") != category:
                continue
            if tag and tag not in (skill.get("tags") or []):
                continue
            results.append(self._public_view(skill))
        return results

    def get_skill(self, skill_id: str) -> dict[str, Any] | None:
        skill = self._catalogue.get(skill_id)
        if skill:
            return {**self._public_view(skill), "skill_md": skill.get("skill_md", "")}
        return None

    def _public_view(self, skill: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": skill["id"],
            "name": skill["name"],
            "category": skill.get("category", ""),
            "tags": skill.get("tags", []),
            "description": skill.get("description", ""),
            "author": skill.get("author", ""),
            "version": skill.get("version", ""),
        }

    # ── Agent skill management ─────────────────────────────────────────

    def list_agent_skills(self, agent_instance_dir: Path) -> list[dict[str, Any]]:
        """List skills installed in an agent's agent_space/skills/ directory."""
        skills_dir = agent_instance_dir / "agent_space" / "skills"
        if not skills_dir.exists():
            return []
        installed = []
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue
            info = self._parse_skill_info(skill_dir.name, skill_md)
            # Check if it's a store skill
            store_skill = self._catalogue.get(skill_dir.name)
            if store_skill:
                info["store_version"] = store_skill.get("version", "")
                info["from_store"] = True
            else:
                info["from_store"] = False
            installed.append(info)
        return installed

    def install_skill(self, agent_instance_dir: Path, skill_id: str) -> dict[str, Any]:
        """Install a skill from the store into an agent."""
        skill = self._catalogue.get(skill_id)
        if not skill:
            raise ValueError(f"Skill '{skill_id}' not found in store")

        skills_dir = agent_instance_dir / "agent_space" / "skills"
        skill_dest = skills_dir / skill_id
        skill_dest.mkdir(parents=True, exist_ok=True)

        # Write SKILL.md
        skill_md_path = skill_dest / "SKILL.md"
        skill_md_path.write_text(skill["skill_md"], encoding="utf-8")

        # Write metadata
        meta = {
            "id": skill["id"],
            "name": skill["name"],
            "version": skill.get("version", "1.0.0"),
            "installed_from": "skill_store",
        }
        meta_path = skill_dest / "skill_meta.json"
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        resource_source_dir = skill.get("resource_source_dir")
        if resource_source_dir:
            source_dir = Path(resource_source_dir)
            if source_dir.exists():
                for item in source_dir.iterdir():
                    if item.name in {"SKILL.md", "skill_meta.json"}:
                        continue
                    destination = skill_dest / item.name
                    if item.is_dir():
                        shutil.copytree(item, destination, dirs_exist_ok=True)
                    elif item.is_file():
                        shutil.copy2(item, destination)

        return {
            "ok": True,
            "skill_id": skill_id,
            "installed_at": str(skill_dest),
            "version": skill.get("version", "1.0.0"),
        }

    def uninstall_skill(self, agent_instance_dir: Path, skill_id: str) -> dict[str, Any]:
        """Remove a skill from an agent."""
        skills_dir = agent_instance_dir / "agent_space" / "skills"
        skill_dir = skills_dir / skill_id
        if not skill_dir.exists():
            raise ValueError(f"Skill '{skill_id}' is not installed")
        shutil.rmtree(skill_dir)
        return {"ok": True, "skill_id": skill_id, "removed": True}

    def _parse_skill_info(self, skill_ref: str, skill_md: Path) -> dict[str, Any]:
        """Parse basic info from a SKILL.md file."""
        import yaml as _yaml

        content = skill_md.read_text(encoding="utf-8")
        frontmatter: dict[str, Any] = {}
        if content.startswith("---"):
            end = content.find("\n---", 3)
            if end != -1:
                try:
                    frontmatter = _yaml.safe_load(content[3:end]) or {}
                except Exception:
                    pass

        return {
            "id": skill_ref,
            "name": str(frontmatter.get("name") or skill_ref),
            "description": str(frontmatter.get("description") or ""),
            "has_slash_command": bool(frontmatter.get("slash_command")),
        }
