"""Builds the per-server client factory that FastMCP's proxy uses to connect.

The factory runs the ``pre_mcp_connect`` hooks and merges their returned headers
over the server record's static headers, then constructs a ``ProxyClient`` for the
upstream. Dynamic/auth headers therefore win over static ones.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastmcp.server.providers.proxy import ProxyClient

from mcp_gateway.hooks import Hooks
from mcp_gateway.models import ServerRecord


def build_client_factory(server: ServerRecord, hooks: Hooks) -> Callable[[], ProxyClient[Any]]:
    """Return a no-arg factory that produces a configured upstream client.

    NOTE: scaffolding stub. Milestone 1 wires ``pre_mcp_connect`` and the
    transport-specific client construction (http/sse/stdio) plus per-server timeout.
    """

    def create_client() -> ProxyClient[Any]:
        raise NotImplementedError("build_client_factory — Milestone 1")

    return create_client
