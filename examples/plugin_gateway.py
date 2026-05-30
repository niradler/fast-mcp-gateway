"""A gateway whose behavior comes entirely from one plugin — exercises every seam.

Where ``live_gateway`` wires loose hooks, this shows the *plugin* packaging: a single
named bundle that contributes hooks, FastMCP middleware (around-the-call control a hook
cannot express), an admin router, an ASGI mount, a registered meta-tool, and
``setup``/``teardown`` bound to the lifespan. The live plugin harness drives it.

Run::

    uv run uvicorn examples.plugin_gateway:app --port 8001

Surfaces:

- MCP endpoint:        http://127.0.0.1:8001/mcp/   (tool ``demo_marco`` is plugin-registered)
- Plugin admin route:  http://127.0.0.1:8001/admin/demo/status
- Plugin ASGI mount:   http://127.0.0.1:8001/mcp/demo-health
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import mcp.types as mt
from fastapi import APIRouter, FastAPI
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from fast_gateway import (
    GatewayContext,
    Hooks,
    PluginContributions,
    SqliteStore,
    ToolCallResult,
    create_gateway,
)


class _DemoMiddleware(Middleware):
    """Around-the-call control: tags every text result so the wrap is observable."""

    def __init__(self, plugin: DemoPlugin) -> None:
        self.plugin = plugin

    async def on_call_tool(self, context: MiddlewareContext[Any], call_next: Any) -> Any:
        response = await call_next(context)
        content = getattr(response, "content", None)
        if isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    block.text = text + " [mw]"
        return response


class DemoPlugin:
    """One plugin that contributes all six kinds of extension, with lifecycle state."""

    name = "demo"

    def __init__(self) -> None:
        self.started = False
        self.torn_down = False
        self.pre_calls = 0

    def contributions(self, context: GatewayContext) -> PluginContributions:
        async def count_calls(
            ctx: MiddlewareContext[mt.CallToolRequestParams],
        ) -> ToolCallResult | None:
            self.pre_calls += 1
            return None

        def register_tools(mcp: FastMCP) -> None:
            @mcp.tool(name="demo_marco", description="Plugin-registered probe tool.")
            def demo_marco() -> str:
                return "polo"

        router = APIRouter()

        @router.get("/status")
        async def status() -> dict[str, Any]:
            return {
                "started": self.started,
                "torn_down": self.torn_down,
                "pre_calls": self.pre_calls,
            }

        async def health(_request: Request) -> JSONResponse:
            return JSONResponse({"plugin": self.name, "ok": True})

        mount = Starlette(routes=[Route("/", health)])

        return PluginContributions(
            hooks=Hooks(pre_tool_call=[count_calls]),
            middleware=[_DemoMiddleware(self)],
            admin_router=router,
            mounts=[("/demo-health", mount)],
            register_tools=register_tools,
        )

    async def setup(self) -> None:
        self.started = True

    async def teardown(self) -> None:
        self.torn_down = True


plugin = DemoPlugin()
gateway = create_gateway(store=SqliteStore(":memory:"), plugins=[plugin])


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with gateway.lifespan(app):
        yield


app = FastAPI(title="Plugin Gateway", lifespan=lifespan)
gateway.install(app)
