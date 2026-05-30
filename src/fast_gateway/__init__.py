"""fast-gateway — a lean, FastMCP-based MCP gateway for FastAPI.

Public API:

    from fast_gateway import create_gateway, Hooks, SqliteStore

See ``create_gateway`` for assembling a gateway and mounting it on a FastAPI app.
"""

from fast_gateway.app import Gateway, create_gateway
from fast_gateway.hooks import (
    ConfirmationContext,
    ConnectContext,
    ConnectSettings,
    Hooks,
    ToolCallResult,
    ToolDecision,
)
from fast_gateway.models import (
    GroupCreate,
    GroupPatch,
    GroupRecord,
    ServerCreate,
    ServerPatch,
    ServerRecord,
    Transport,
)
from fast_gateway.plugins import GatewayContext, Plugin, PluginContributions
from fast_gateway.store import SqliteStore, Store

__version__ = "0.0.2"

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
