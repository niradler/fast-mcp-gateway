"""``create_gateway`` — assemble the gateway and hand back the pieces to mount.

The gateway is a parent FastMCP server (exposed as an ASGI app via ``http_app``)
plus a FastAPI admin router for registry CRUD. ``create_gateway`` wires the hook
middleware, the builder, and the meta-tools, then returns a :class:`Gateway` the
caller mounts onto their own FastAPI app.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from fastmcp import FastMCP

from mcp_gateway.access import AccessPolicy
from mcp_gateway.api.groups import build_groups_router
from mcp_gateway.api.servers import build_servers_router
from mcp_gateway.builder import GatewayBuilder
from mcp_gateway.catalog import catalog_tool_to_fastmcp
from mcp_gateway.hooks import HookMiddleware, Hooks, merge_hooks
from mcp_gateway.plugins import GatewayContext
from mcp_gateway.routing import GroupDispatch
from mcp_gateway.search import register_search_tools
from mcp_gateway.store.base import Store

if TYPE_CHECKING:
    from fastmcp.server.http import StarletteWithLifespan
    from fastmcp.tools.base import Tool
    from starlette.applications import Starlette
    from starlette.types import Lifespan

    from mcp_gateway.plugins import Plugin


@dataclass
class Gateway:
    """The assembled gateway: an MCP ASGI app, an admin router, and a reload hook.

    Mount it onto your own FastAPI app with :meth:`install`. The MCP server needs a
    lifespan to manage sessions, so the host app must be created with
    ``FastAPI(lifespan=gateway.lifespan)``.
    """

    mcp: FastMCP
    mcp_app: StarletteWithLifespan
    admin_router: APIRouter
    builder: GatewayBuilder
    _lifespan: Lifespan[Starlette]
    _transport_path: str

    @property
    def lifespan(self) -> Lifespan[Starlette]:
        """The lifespan to pass to ``FastAPI(lifespan=...)`` on the host app.

        It initializes the store, builds the proxy mounts from the registry, and
        then runs the underlying MCP app's lifespan (session management).
        """
        return self._lifespan

    async def reload(self) -> None:
        """Rebuild proxy mounts from the current registry."""
        await self.builder.reload()

    def install(
        self,
        app: FastAPI,
        *,
        mcp_path: str = "/mcp",
        admin_prefix: str = "/admin",
        group_segment: str = "g",
    ) -> None:
        """Mount the MCP app and admin router onto an existing FastAPI app.

        The host ``app`` must already have been created with
        ``FastAPI(lifespan=gateway.lifespan)``.

        Exposes the full catalog at ``mcp_path`` and a per-group view at
        ``{mcp_path}/{group_segment}/{group}`` (e.g. ``/mcp/g/analytics``) served
        by the same shared MCP app. The group mount is registered first so its
        more specific prefix matches before the full mount.
        """
        app.include_router(self.admin_router, prefix=admin_prefix)
        group_mount = f"{mcp_path}/{group_segment}"
        app.mount(group_mount, GroupDispatch(self.mcp_app, self._transport_path))
        app.mount(mcp_path, self.mcp_app)


def create_gateway(
    store: Store,
    hooks: Hooks | None = None,
    *,
    plugins: Sequence[Plugin] = (),
    name: str = "MCP Gateway",
    mcp_path: str = "/",
) -> Gateway:
    """Build a :class:`Gateway` over ``store`` with the given ``hooks`` and ``plugins``.

    Mounts an empty parent FastMCP server (no upstreams until :meth:`Gateway.reload`)
    with the hook middleware and meta-tools attached, alongside the admin CRUD router.
    Each plugin's :class:`~mcp_gateway.plugins.PluginContributions` is applied in
    registration order: hooks are merged, FastMCP middleware is added, meta-tools are
    registered, the admin router is extended, and ASGI sub-apps are mounted. Plugin
    ``setup`` / ``teardown`` are driven from the gateway lifespan.
    """
    base_hooks = hooks or Hooks()
    policy = AccessPolicy()
    mcp: FastMCP = FastMCP(name)

    async def _reload() -> None:
        await builder.reload()  # `builder` is assigned later in this scope; only called at runtime

    context = GatewayContext(store=store, mcp=mcp, reload=_reload)
    contributions = [p.contributions(context) for p in plugins]
    effective_hooks = merge_hooks(base_hooks, *(c.hooks for c in contributions))

    async def _catalog_tools() -> Sequence[Tool]:
        local = await mcp.local_provider.list_tools()
        persisted = [catalog_tool_to_fastmcp(t) for t in await store.list_catalog()]
        return [*local, *persisted]

    mcp.add_middleware(HookMiddleware(effective_hooks, policy, catalog=_catalog_tools))
    for c in contributions:
        for mw in c.middleware:
            mcp.add_middleware(mw)

    register_search_tools(mcp, store, policy)
    for c in contributions:
        if c.register_tools is not None:
            c.register_tools(mcp)

    builder = GatewayBuilder(mcp=mcp, store=store, hooks=effective_hooks, policy=policy)
    admin_router = _build_admin_router(store, builder, effective_hooks)
    for plugin, c in zip(plugins, contributions, strict=True):
        if c.admin_router is not None:
            admin_router.include_router(c.admin_router, prefix=f"/{plugin.name}")

    mcp_app = mcp.http_app(path=mcp_path)
    for c in contributions:
        for path, sub_app in c.mounts:
            mcp_app.mount(path, sub_app)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await store.initialize()
        for plugin in plugins:
            await plugin.setup()
        await builder.reload()
        try:
            async with mcp_app.lifespan(app):
                yield
        finally:
            for plugin in reversed(list(plugins)):
                await plugin.teardown()

    return Gateway(
        mcp=mcp,
        mcp_app=mcp_app,
        admin_router=admin_router,
        builder=builder,
        _lifespan=lifespan,
        _transport_path=mcp_path,
    )


def _build_admin_router(store: Store, builder: GatewayBuilder, hooks: Hooks) -> APIRouter:
    """Combine the server and group routers and add the reload endpoint."""
    router = APIRouter()
    router.include_router(build_servers_router(store, hooks))
    router.include_router(build_groups_router(store))

    @router.post("/reload", tags=["admin"])
    async def reload() -> dict[str, str]:
        """Rebuild proxy mounts from the current registry."""
        await builder.reload()
        return {"status": "reloaded"}

    return router
