"""Builds the per-server async client factory that FastMCP's proxy uses to connect.

Runs ``pre_mcp_connect`` hooks on each call; hook headers layer over static ones
(dynamic/auth headers win). An async factory is required because hooks must be awaited.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient

from fast_mcp_gateway.hooks import ConnectContext, Hooks
from fast_mcp_gateway.models import ServerRecord, Transport

ClientFactory = Callable[[], Awaitable[ProxyClient[Any]]]


async def resolve_connect_settings(
    server: ServerRecord, hooks: Hooks
) -> tuple[dict[str, str], float]:
    """Run ``pre_mcp_connect`` hooks and return the effective headers and timeout.

    Headers start from the server's static headers and each hook's headers are
    layered on top (so dynamic/auth headers win). A hook may override the timeout;
    the last hook to set one wins.
    """
    headers = dict(server.static_headers)
    timeout = server.timeout_seconds
    context = ConnectContext(server=server)

    for hook in hooks.pre_mcp_connect:
        settings = await hook(context)
        if settings is None:
            continue
        headers.update(settings.headers)
        if settings.timeout_seconds is not None:
            timeout = settings.timeout_seconds

    return headers, timeout


def _build_transport(
    server: ServerRecord, headers: dict[str, str]
) -> StreamableHttpTransport | SSETransport:
    if server.transport is Transport.SSE:
        return SSETransport(server.url, headers=headers)
    return StreamableHttpTransport(server.url, headers=headers)


def build_client_factory(server: ServerRecord, hooks: Hooks) -> ClientFactory:
    """Return an async factory that produces a configured upstream client.

    Each call resolves the connect settings (running the hooks), builds a transport
    for the server's protocol, and wraps it in a ``ProxyClient`` with the timeout.
    """

    async def create_client() -> ProxyClient[Any]:
        headers, timeout = await resolve_connect_settings(server, hooks)
        transport = _build_transport(server, headers)
        return ProxyClient(transport, timeout=timeout)

    return create_client
