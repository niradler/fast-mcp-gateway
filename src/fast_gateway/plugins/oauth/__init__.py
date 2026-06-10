"""fast_gateway.plugins.oauth — upstream OAuth browser flow for the CLI/daemon mode."""

from __future__ import annotations

from fast_gateway.plugins.oauth.plugin import (
    OAuthPlugin,
    build_oauth,
    default_oauth_token_dir,
)

__all__ = ["OAuthPlugin", "build_oauth", "default_oauth_token_dir"]
