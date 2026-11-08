"""Serve me_agent as an authenticated Foundry AgentService local package.

Foundry injects AGENT_SECRET through spec.runtime.config_env. Keep the package
fail-closed at startup, and use the SDK's shared authentication implementation.
"""
from __future__ import annotations

import os

from ccfoundry_agent_kit.auth import AgentSecretAuth

if not os.environ.get("AGENT_SECRET", "").strip():
    raise RuntimeError(
        "AGENT_SECRET is not set. Foundry injects it only when the manifest "
        "lists AGENT_SECRET under spec.runtime.config_env."
    )

from me_agent_example.app import app as agent_app

app = AgentSecretAuth(
    agent_app,
    get_secret=lambda: os.environ.get("AGENT_SECRET", "").strip(),
    required=True,
)
