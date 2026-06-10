"""fast_gateway.plugins.oauth — upstream OAuth for the gateway.

Two grants: the interactive browser authorization-code flow (primed with
``fast-gateway login``) and the headless ``client_credentials`` grant, whose
pieces are importable standalone — ``client_credentials_hook()`` slots into
``Hooks(pre_mcp_connect=[...])`` without registering the full plugin.
"""

from __future__ import annotations

from fast_gateway.plugins.oauth.client_credentials import (
    ClientCredentialsAuth,
    build_client_credentials,
    client_credentials_hook,
)
from fast_gateway.plugins.oauth.plugin import (
    OAuthPlugin,
    build_oauth,
    default_oauth_token_dir,
)

__all__ = [
    "ClientCredentialsAuth",
    "OAuthPlugin",
    "build_client_credentials",
    "build_oauth",
    "client_credentials_hook",
    "default_oauth_token_dir",
]
