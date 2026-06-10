"""OAuthPlugin: upstream OAuth browser flow as a Mode-B (CLI/daemon) gateway plugin.

Not registered in Mode-A (``create_gateway`` called directly). OAuth requires a
human at a terminal to complete the browser flow and is unsuitable for headless
library embedding. ``build_app`` registers it unconditionally; non-OAuth servers
see it as a no-op.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastmcp.client.auth import OAuth
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)

from fast_gateway.hooks import ConnectContext, ConnectSettings, Hooks
from fast_gateway.models import ServerAuth, ServerRecord
from fast_gateway.plugins import GatewayContext, PluginContributions
from fast_gateway.plugins.oauth.client_credentials import client_credentials_hook

logger = logging.getLogger("fast_gateway.plugins.oauth")


def default_oauth_token_dir() -> Path:
    """Return the directory used for persistent OAuth token storage.

    When ``FAST_GATEWAY_OAUTH_DIR`` is set that path is used; otherwise
    ``~/.fast-gateway/oauth``. On POSIX the directory is created 0700 (owner-only)
    because it holds refresh tokens at rest; on Windows chmod is a no-op.
    """
    env = os.environ.get("FAST_GATEWAY_OAUTH_DIR")
    token_dir = Path(env) if env else Path.home() / ".fast-gateway" / "oauth"
    token_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        try:
            token_dir.chmod(0o700)
        except OSError as exc:
            logger.warning(
                "Could not restrict permissions on OAuth token dir %s (holds refresh tokens); "
                "verify it is not readable by other users. Cause: %s",
                token_dir,
                exc,
            )
    return token_dir


class _NonInteractiveOAuth(OAuth):
    """OAuth provider for the daemon: raises immediately instead of opening a browser.

    The daemon must never block on a browser flow. When an upstream token expires
    and cannot be refreshed, this raises so ``collect_catalog``'s per-server
    error isolation handles it gracefully. Interactive login is a CLI-only action.
    """

    _login_hint: str

    def __init__(self, *args: Any, login_hint: str, **kwargs: Any) -> None:
        self._login_hint = login_hint
        super().__init__(*args, **kwargs)

    async def redirect_handler(self, authorization_url: str) -> None:
        """Raise immediately; daemon never opens a browser.

        Operators must run ``fast-gateway login <name>`` on a machine with a browser
        to prime the token cache before or after the daemon starts.
        """
        raise RuntimeError(
            f"Upstream requires interactive OAuth login. Run: fast-gateway login {self._login_hint}"
        )


def build_oauth(server: ServerRecord, *, interactive: bool = True) -> OAuth:
    """Return an OAuth client provider for *server*.

    ``interactive=True`` (default, CLI ``login``) returns the standard ``OAuth``
    that opens a browser. ``interactive=False`` (daemon) returns
    ``_NonInteractiveOAuth`` that raises on redirect instead of blocking 300 s.
    Both share the same ``FileTreeStore`` token cache.
    """
    scopes: list[str] | None = server.oauth_scopes if server.oauth_scopes else None
    data_dir = default_oauth_token_dir()
    token_storage: FileTreeStore = FileTreeStore(
        data_directory=data_dir,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory=data_dir),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(
            directory=data_dir
        ),
    )
    if interactive:
        return OAuth(
            mcp_url=server.url,
            scopes=scopes,
            client_name="fast-gateway",
            token_storage=token_storage,
        )
    return _NonInteractiveOAuth(
        mcp_url=server.url,
        scopes=scopes,
        client_name="fast-gateway",
        token_storage=token_storage,
        login_hint=server.name,
    )


class OAuthPlugin:
    """Plugin that attaches OAuth auth to upstream transports at connect time.

    Auto-registered by ``build_app``; a no-op for non-OAuth servers. ``OAUTH`` gets
    the browser authorization-code provider (primed via ``fast-gateway login``);
    ``OAUTH_CLIENT_CREDENTIALS`` gets the headless machine-to-machine provider.
    Both flow through the generic transport seam — no OAuth logic in core.
    """

    name = "oauth"

    def __init__(self) -> None:
        self._client_credentials = client_credentials_hook()

    def contributions(self, context: GatewayContext) -> PluginContributions:
        """Return a single ``pre_mcp_connect`` hook that attaches OAuth when needed."""
        return PluginContributions(hooks=Hooks(pre_mcp_connect=[self._attach_oauth]))

    async def _attach_oauth(self, ctx: ConnectContext) -> ConnectSettings | None:
        if ctx.server.auth is ServerAuth.OAUTH:
            return ConnectSettings(auth=build_oauth(ctx.server, interactive=False))
        return await self._client_credentials(ctx)
