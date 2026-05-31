"""OAuthPlugin: upstream OAuth browser flow as a Mode-B (CLI/daemon) gateway plugin.

Not registered in Mode-A (``create_gateway`` called directly). OAuth requires a
human at a terminal to complete the browser flow and is unsuitable for headless
library embedding. ``build_app`` registers it unconditionally; non-OAuth servers
see it as a no-op.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastmcp.client.auth import OAuth
from key_value.aio.stores.filetree import FileTreeStore

from fast_gateway.hooks import ConnectContext, ConnectSettings, Hooks
from fast_gateway.models import ServerAuth, ServerRecord
from fast_gateway.plugins import GatewayContext, PluginContributions


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


class OAuthPlugin:
    """Mode-B-only plugin that attaches OAuth auth to upstream transports at connect time.

    Auto-registered by ``build_app``. For non-OAuth servers the hook is a no-op.
    For servers with ``auth=ServerAuth.OAUTH`` it returns ``ConnectSettings(auth=...)``
    so the generic transport seam carries the provider — no OAuth logic in core.
    """

    name = "oauth"

    def contributions(self, context: GatewayContext) -> PluginContributions:
        """Return a single ``pre_mcp_connect`` hook that attaches OAuth when needed."""
        return PluginContributions(hooks=Hooks(pre_mcp_connect=[self._attach_oauth]))

    async def _attach_oauth(self, ctx: ConnectContext) -> ConnectSettings | None:
        if ctx.server.auth is ServerAuth.OAUTH:
            return ConnectSettings(auth=build_oauth(ctx.server))
        return None
