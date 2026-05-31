"""Builds the per-server async client factory that FastMCP's proxy uses to connect.

Runs ``pre_mcp_connect`` hooks on each call; hook headers layer over static ones
(dynamic/auth headers win). An async factory is required because hooks must be awaited.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastmcp.client.auth import OAuth
from fastmcp.client.transports.http import StreamableHttpTransport
from fastmcp.client.transports.sse import SSETransport
from fastmcp.server.providers.proxy import ProxyClient
from key_value.aio.stores.filetree import FileTreeStore

from fast_gateway.hooks import ConnectContext, Hooks
from fast_gateway.models import ServerAuth, ServerRecord, Transport

ClientFactory = Callable[[], Awaitable[ProxyClient[Any]]]


def default_oauth_token_dir() -> Path:
    """Return the directory used for persistent OAuth token storage.

    When ``FAST_GATEWAY_OAUTH_DIR`` is set that path is used; otherwise the
    default is ``~/.fast-gateway/oauth``. The directory is created if absent.
    """
    env = os.environ.get("FAST_GATEWAY_OAUTH_DIR")
    token_dir = Path(env) if env else Path.home() / ".fast-gateway" / "oauth"
    token_dir.mkdir(parents=True, exist_ok=True)
    return token_dir


def build_oauth(server: ServerRecord) -> OAuth:
    """Return a persistent OAuth client provider for the given server.

    Tokens are stored in a shared ``FileTreeStore`` directory so the CLI login
    process and the serve daemon both read/write the same cache. FastMCP
    namespaces token entries by server URL internally, so a single directory is
    safe for multiple servers.
    """
    scopes: list[str] | None = server.oauth_scopes if server.oauth_scopes else None
    token_storage: FileTreeStore = FileTreeStore(data_directory=default_oauth_token_dir())
    return OAuth(
        mcp_url=server.url,
        scopes=scopes,
        client_name="fast-gateway",
        token_storage=token_storage,
    )


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
    if server.auth is ServerAuth.OAUTH:
        oauth = build_oauth(server)
        if server.transport is Transport.SSE:
            return SSETransport(server.url, headers=headers, auth=oauth)
        return StreamableHttpTransport(server.url, headers=headers, auth=oauth)
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
