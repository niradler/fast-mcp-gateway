"""Plugins: named bundles of gateway extensions.

A plugin groups hooks (the gateway's primary extension mechanism) and, optionally,
FastMCP middleware (for around-the-call control such as circuit breaking, retry, or
sandboxing — things the ``pre_tool_call`` / ``post_tool_call`` seams cannot express
because they need to wrap execution of the upstream call itself), an admin
``APIRouter``, ASGI sub-app mounts (e.g. a third-party governance HTTP API), MCP
meta-tool registration, and async ``setup`` / ``teardown`` bound to the gateway
lifespan. ``create_gateway`` applies every plugin's contributions when assembling
the gateway.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from mcp_gateway.hooks import Hooks

if TYPE_CHECKING:
    from fastapi import APIRouter
    from fastmcp import FastMCP
    from fastmcp.server.middleware import Middleware
    from starlette.types import ASGIApp


@dataclass
class PluginContributions:
    """Everything a plugin adds to the gateway. Every field is optional.

    - ``hooks``: merged into the gateway's hook chain (all five seams).
    - ``middleware``: FastMCP middleware added to the parent server, for control
      that wraps the upstream call (e.g. circuit breaker / retry / sandbox).
    - ``admin_router``: mounted under ``<admin_prefix>/<plugin name>``.
    - ``mounts``: ``(path, asgi_app)`` pairs mounted on the MCP ASGI app.
    - ``register_tools``: called with the parent ``FastMCP`` to add meta-tools.
    """

    hooks: Hooks = field(default_factory=Hooks)
    middleware: list[Middleware] = field(default_factory=list)
    admin_router: APIRouter | None = None
    mounts: list[tuple[str, ASGIApp]] = field(default_factory=list)
    register_tools: Callable[[FastMCP], None] | None = None


@runtime_checkable
class Plugin(Protocol):
    """A named extension bundle applied at ``create_gateway`` time."""

    name: str

    def contributions(self) -> PluginContributions:
        """Return the hooks / middleware / endpoints / tools this plugin adds."""
        ...

    async def setup(self) -> None:
        """Run once at gateway startup (load config, open sinks, fetch feeds)."""
        ...

    async def teardown(self) -> None:
        """Run once at gateway shutdown (flush sinks, close connections)."""
        ...
