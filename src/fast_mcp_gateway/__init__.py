"""fast-mcp-gateway — a lean, FastMCP-based MCP gateway for FastAPI.

Public API:

    from fast_mcp_gateway import create_gateway, Hooks, SqliteStore

See ``create_gateway`` for assembling a gateway and mounting it on a FastAPI app.
"""

from fast_mcp_gateway.app import Gateway, create_gateway
from fast_mcp_gateway.hooks import (
    ConfirmationContext,
    ConnectContext,
    ConnectSettings,
    Hooks,
    ToolCallResult,
    ToolDecision,
)
from fast_mcp_gateway.models import (
    GroupCreate,
    GroupPatch,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
    Transport,
)
from fast_mcp_gateway.plugins import GatewayContext, Plugin, PluginContributions
from fast_mcp_gateway.store import SqliteStore, Store

__version__ = "0.0.1"

__all__ = [
    "ConfirmationContext",
    "ConnectContext",
    "ConnectSettings",
    "Gateway",
    "GatewayContext",
    "GroupCreate",
    "GroupPatch",
    "GroupRecord",
    "Hooks",
    "Plugin",
    "PluginContributions",
    "ServerCreate",
    "ServerPatch",
    "ServerRecord",
    "SqliteStore",
    "Store",
    "ToolCallResult",
    "ToolDecision",
    "Transport",
    "__version__",
    "create_gateway",
]
