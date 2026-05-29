"""``create_gateway`` — assemble the gateway and hand back the pieces to mount.

The gateway is a parent FastMCP server (exposed as an ASGI app via ``http_app``)
plus a FastAPI admin router for registry CRUD. ``create_gateway`` wires the hook
middleware, the builder, and the meta-tools, then returns a :class:`Gateway` the
caller mounts onto their own FastAPI app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter, FastAPI
from fastmcp import FastMCP

from mcp_gateway.api.groups import build_groups_router
from mcp_gateway.api.servers import build_servers_router
from mcp_gateway.builder import GatewayBuilder
from mcp_gateway.hooks import HookMiddleware, Hooks
from mcp_gateway.search import register_search_tools
from mcp_gateway.store.base import Store

if TYPE_CHECKING:
    from fastmcp.server.http import StarletteWithLifespan
    from starlette.applications import Starlette
    from starlette.types import Lifespan


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

    @property
    def lifespan(self) -> Lifespan[Starlette]:
        """The lifespan to pass to ``FastAPI(lifespan=...)`` on the host app."""
        return self.mcp_app.lifespan

    async def reload(self) -> None:
        """Rebuild proxy mounts from the current registry."""
        await self.builder.reload()

    def install(
        self, app: FastAPI, *, mcp_path: str = "/mcp", admin_prefix: str = "/admin"
    ) -> None:
        """Mount the MCP app and admin router onto an existing FastAPI app.

        The host ``app`` must already have been created with
        ``FastAPI(lifespan=gateway.lifespan)``.
        """
        app.include_router(self.admin_router, prefix=admin_prefix)
        app.mount(mcp_path, self.mcp_app)


def create_gateway(
    store: Store,
    hooks: Hooks | None = None,
    *,
    name: str = "MCP Gateway",
    mcp_path: str = "/",
) -> Gateway:
    """Build a :class:`Gateway` over ``store`` with the given ``hooks``.

    Mounts an empty parent FastMCP server (no upstreams until :meth:`Gateway.reload`)
    with the hook middleware and meta-tools attached, alongside the admin CRUD router.
    """
    hooks = hooks or Hooks()

    mcp: FastMCP = FastMCP(name)
    mcp.add_middleware(HookMiddleware(hooks))
    register_search_tools(mcp)

    builder = GatewayBuilder(mcp=mcp, store=store, hooks=hooks)
    admin_router = _build_admin_router(store, builder)
    mcp_app = mcp.http_app(path=mcp_path)

    return Gateway(mcp=mcp, mcp_app=mcp_app, admin_router=admin_router, builder=builder)


def _build_admin_router(store: Store, builder: GatewayBuilder) -> APIRouter:
    """Combine the server and group routers and add the reload endpoint."""
    router = APIRouter()
    router.include_router(build_servers_router(store))
    router.include_router(build_groups_router(store))

    @router.post("/reload", tags=["admin"])
    async def reload() -> dict[str, str]:
        """Rebuild proxy mounts from the current registry."""
        await builder.reload()
        return {"status": "reloaded"}

    return router
