"""fast-gateway — a lean, FastMCP-based MCP gateway for FastAPI.

Public API:

    from fast_gateway import create_gateway, Hooks, SqliteStore

See ``create_gateway`` for assembling a gateway and mounting it on a FastAPI app.
"""

from fast_gateway.app import Gateway, create_gateway
from fast_gateway.config import GatewayConfig, HilConfig, LocalPolicy, load_config, load_policy
from fast_gateway.factory import build_app
from fast_gateway.hil import HumanApprovalPlugin
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
    ServerAuth,
    ServerCreate,
    ServerPatch,
    ServerRecord,
    Transport,
)
from fast_gateway.plugins import GatewayContext, Plugin, PluginContributions
from fast_gateway.plugins.oauth import OAuthPlugin
from fast_gateway.reference import audit_hook, confirm_hook, deny_hook
from fast_gateway.store import SqliteStore, Store

__version__ = "0.0.3"

__all__ = [
    "ConfirmationContext",
    "ConnectContext",
    "ConnectSettings",
    "Gateway",
    "GatewayConfig",
    "GatewayContext",
    "GroupCreate",
    "GroupPatch",
    "GroupRecord",
    "HilConfig",
    "Hooks",
    "HumanApprovalPlugin",
    "LocalPolicy",
    "OAuthPlugin",
    "Plugin",
    "PluginContributions",
    "ServerAuth",
    "ServerCreate",
    "ServerPatch",
    "ServerRecord",
    "SqliteStore",
    "Store",
    "ToolCallResult",
    "ToolDecision",
    "Transport",
    "__version__",
    "audit_hook",
    "build_app",
    "confirm_hook",
    "create_gateway",
    "deny_hook",
    "load_config",
    "load_policy",
]
