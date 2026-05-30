"""Gateway governed by the agent-os policy plugin, over a real proxied upstream.

Requires the optional ``agt`` extra (``uv sync --extra agt``). Wires
:class:`AgtAgentOsPlugin` with an in-memory policy that denies one namespaced tool
(``echo_purge_cache``); every other tool is allowed. Seeds the local ``echo`` upstream
so a live HTTP call can be checked against the policy.

Run the upstream and this app::

    uv run uvicorn examples.echo_upstream:app --port 9100
    uv run uvicorn examples.agentos_gateway:app --port 8002

Then a call to ``echo_echo`` succeeds and ``echo_purge_cache`` is denied by policy.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator

from agent_os.policies import (
    PolicyAction,
    PolicyCondition,
    PolicyDocument,
    PolicyOperator,
    PolicyRule,
)
from fastapi import FastAPI

from fast_gateway import ServerCreate, SqliteStore, Store, create_gateway
from fast_gateway.plugins.agentos import AgtAgentOsPlugin, AgtAgentOsSettings

ECHO_URL = os.environ.get("ECHO_URL", "http://127.0.0.1:9100/mcp/")

_DENY_PURGE = PolicyDocument(
    version="1.0",
    name="gateway-policy",
    rules=[
        PolicyRule(
            name="no-purge",
            condition=PolicyCondition(
                field="resource", operator=PolicyOperator.EQ, value="echo_purge_cache"
            ),
            action=PolicyAction.DENY,
            message="echo_purge_cache is denied by agent-os policy",
        )
    ],
)


async def seed_registry(store: Store) -> None:
    """Register the echo upstream once so policy can be checked on a live tool."""
    if not await store.list_servers():
        await store.create_server(ServerCreate(name="echo", url=ECHO_URL))


store = SqliteStore(os.environ.get("GATEWAY_DB", "agentos_gateway.db"))
gateway = create_gateway(
    store=store,
    plugins=[AgtAgentOsPlugin(AgtAgentOsSettings(policies=[_DENY_PURGE]))],
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run the gateway lifespan, then seed and reload once on first boot."""
    async with gateway.lifespan(app):
        await seed_registry(store)
        await gateway.reload()
        yield


app = FastAPI(title="Agent-OS Governed Gateway", lifespan=lifespan)
gateway.install(app)
