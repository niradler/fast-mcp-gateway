"""Plugins: named bundles of gateway extensions applied at ``create_gateway`` time.

Bundles hooks, FastMCP middleware (for around-the-call control that hook seams cannot
express), admin router, ASGI mounts, meta-tool registration, and lifespan
``setup``/``teardown``. Each plugin returns a :class:`PluginContributions` from its
``contributions(ctx)`` method.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from fastapi import APIRouter
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware
from starlette.types import ASGIApp

from fast_mcp_gateway.hooks import Hooks
from fast_mcp_gateway.store.base import Store


@dataclass
class GatewayContext:
    """Handle to gateway internals, passed to each plugin's ``contributions``.

    Lets a plugin read the registry (``store``), register state on the parent
    ``mcp``, and trigger a registry ``reload`` (e.g. after mutating servers/groups).
    """

    store: Store
    mcp: FastMCP
    reload: Callable[[], Awaitable[None]]


@dataclass
class PluginContributions:
    """Everything a plugin adds to the gateway; all fields optional.

    ``middleware`` wraps the upstream call (e.g. circuit breaker/retry) — use it for
    control the hook seams cannot express. ``admin_router`` is mounted under
    ``<admin_prefix>/<plugin name>``; ``register_tools`` receives the parent ``FastMCP``.
    """

    hooks: Hooks = field(default_factory=Hooks)
    middleware: list[Middleware] = field(default_factory=list)
    admin_router: APIRouter | None = None
    mounts: list[tuple[str, ASGIApp]] = field(default_factory=list)
    register_tools: Callable[[FastMCP], None] | None = None


@runtime_checkable
class Plugin(Protocol):
    """A named extension bundle applied at ``create_gateway`` time.

    Only ``name`` and ``contributions`` are required. A plugin may additionally define
    async ``setup`` / ``teardown`` methods; when present, the gateway lifespan runs them
    at startup and shutdown (load config / open sinks, then flush / close).
    """

    name: str

    def contributions(self, context: GatewayContext) -> PluginContributions:
        """Return the hooks / middleware / endpoints / tools this plugin adds."""
        ...
