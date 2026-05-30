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

from fastapi import APIRouter, FastAPI, params
from fastmcp import FastMCP
from fastmcp.server.http import StarletteWithLifespan
from fastmcp.tools.base import Tool
from starlette.applications import Starlette
from starlette.types import Lifespan

from fast_mcp_gateway.access import AccessPolicy
from fast_mcp_gateway.api.groups import build_groups_router
from fast_mcp_gateway.api.servers import build_servers_router
from fast_mcp_gateway.builder import GatewayBuilder
from fast_mcp_gateway.catalog import catalog_tool_to_fastmcp
from fast_mcp_gateway.hooks import HookMiddleware, Hooks, merge_hooks
from fast_mcp_gateway.plugins import GatewayContext, Plugin
from fast_mcp_gateway.routing import GroupDispatch
from fast_mcp_gateway.search import register_search_tools
from fast_mcp_gateway.store.base import Store


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
        admin_dependencies: Sequence[params.Depends] | None = None,
    ) -> None:
        """Mount the MCP app and admin router onto an existing FastAPI app.

        The host ``app`` must already have been created with ``FastAPI(lifespan=gateway.lifespan)``.
        Group view is mounted before the full catalog mount so its specific prefix wins.
        ``admin_dependencies`` guards every admin route (the router itself has no auth).
        """
        app.include_router(self.admin_router, prefix=admin_prefix, dependencies=admin_dependencies)
        group_mount = f"{mcp_path}/{group_segment}"
        app.mount(group_mount, GroupDispatch(self.mcp_app, self._transport_path))
        app.mount(mcp_path, self.mcp_app)


def create_gateway(
    store: Store,
    hooks: Hooks | None = None,
    *,
    plugins: Sequence[Plugin] = (),
    name: str = "MCP Gateway",
    transport_path: str = "/",
) -> Gateway:
    """Build a :class:`Gateway` over ``store`` with the given ``hooks`` and ``plugins``.

    Wires hook middleware, meta-tools, and admin router; merges plugin contributions
    (hooks, middleware, tools, router, ASGI mounts) in registration order; drives plugin
    ``setup``/``teardown`` from the lifespan. ``transport_path`` is the MCP transport
    sub-path inside the ASGI app, distinct from the ``mcp_path`` in :meth:`Gateway.install`.
    """
    base_hooks = hooks or Hooks()
    policy = AccessPolicy()
    mcp: FastMCP = FastMCP(name)

    async def _reload() -> None:
        await builder.reload()

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

    mcp_app = mcp.http_app(path=transport_path)
    for c in contributions:
        for path, sub_app in c.mounts:
            mcp_app.mount(path, sub_app)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await store.initialize()
        started: list[Plugin] = []
        try:
            for plugin in plugins:
                setup = getattr(plugin, "setup", None)
                if setup is not None:
                    await setup()
                started.append(plugin)
            await builder.reload()
            async with mcp_app.lifespan(app):
                yield
        finally:
            for plugin in reversed(started):
                teardown = getattr(plugin, "teardown", None)
                if teardown is not None:
                    await teardown()
            await store.close()

    return Gateway(
        mcp=mcp,
        mcp_app=mcp_app,
        admin_router=admin_router,
        builder=builder,
        _lifespan=lifespan,
        _transport_path=transport_path,
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
