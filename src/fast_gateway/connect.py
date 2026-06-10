"""Builds the per-server async client factory that FastMCP's proxy uses to connect.

Runs ``pre_mcp_connect`` hooks; hook headers layer over static ones (dynamic wins).
Static header values may embed ``${env:}``/``${file:}`` secret references, resolved
here at connect time so credentials never live in the registry. ``ConnectSettings.auth``
carries an httpx-compatible auth provider; the last hook to set one wins.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient

from fast_gateway.hooks import ConnectContext, Hooks
from fast_gateway.models import ServerRecord, Transport
from fast_gateway.secret_refs import resolve_header_refs

ClientFactory = Callable[[], Awaitable[ProxyClient[Any]]]


async def resolve_connect_settings(
    server: ServerRecord, hooks: Hooks
) -> tuple[dict[str, str], float, Any | None]:
    """Run ``pre_mcp_connect`` hooks and return the effective headers, timeout, and auth.

    Headers start from the server's static headers — with ``${env:}``/``${file:}``
    secret refs resolved — and each hook's headers layer on top, passed through
    verbatim. A hook may override the timeout or supply an auth provider; the last
    hook to set each wins.
    """
    headers = resolve_header_refs(server.static_headers)
    timeout = server.timeout_seconds
    auth: Any | None = None
    context = ConnectContext(server=server)

    for hook in hooks.pre_mcp_connect:
        settings = await hook(context)
        if settings is None:
            continue
        headers.update(settings.headers)
        if settings.timeout_seconds is not None:
            timeout = settings.timeout_seconds
        if settings.auth is not None:
            auth = settings.auth

    return headers, timeout, auth


def _build_transport(
    server: ServerRecord, headers: dict[str, str], auth: Any | None
) -> StreamableHttpTransport | SSETransport:
    if server.transport is Transport.SSE:
        return SSETransport(server.url, headers=headers, auth=auth)
    return StreamableHttpTransport(server.url, headers=headers, auth=auth)


def build_client_factory(server: ServerRecord, hooks: Hooks) -> ClientFactory:
    """Return an async factory that produces a configured upstream client.

    Each call resolves the connect settings (running the hooks), builds a transport
    for the server's protocol, and wraps it in a ``ProxyClient`` with the timeout.
    """

    async def create_client() -> ProxyClient[Any]:
        headers, timeout, auth = await resolve_connect_settings(server, hooks)
        transport = _build_transport(server, headers, auth)
        return ProxyClient(transport, timeout=timeout)

    return create_client
