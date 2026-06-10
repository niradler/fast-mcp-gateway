"""OAuth2 client-credentials grant (machine-to-machine) for upstream MCP servers.

Fully headless: no browser, no on-disk token cache — tokens live in process memory
and refresh before expiry (and once on a 401). Usable standalone: add
``client_credentials_hook()`` to ``Hooks(pre_mcp_connect=[...])`` or build
:class:`ClientCredentialsAuth` directly, without registering ``OAuthPlugin``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Generator

import httpx

from fast_gateway.hooks import ConnectContext, ConnectHook, ConnectSettings
from fast_gateway.models import ServerAuth, ServerRecord
from fast_gateway.secret_refs import resolve_secret_refs

logger = logging.getLogger("fast_gateway.plugins.oauth")

_EXPIRY_SKEW_SECONDS = 60.0
_DEFAULT_EXPIRES_IN_SECONDS = 3600.0


class ClientCredentialsAuth(httpx.Auth):
    """httpx auth provider implementing the OAuth2 ``client_credentials`` grant.

    Fetches a bearer token from the token endpoint (credentials sent in the form
    body — ``client_secret_post``), caches it in memory until shortly before
    expiry, and force-refreshes once when the upstream answers 401. Async-only,
    matching the gateway's async transports.
    """

    def __init__(
        self,
        token_url: str,
        client_id: str,
        client_secret: str,
        scopes: list[str] | None = None,
        *,
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._scopes = list(scopes or [])
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._lock = asyncio.Lock()
        self._access_token: str | None = None
        self._expires_at = 0.0

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        request.headers["Authorization"] = f"Bearer {await self._get_token()}"
        response = yield request
        if response.status_code == 401:
            token = await self._get_token(force_refresh=True)
            request.headers["Authorization"] = f"Bearer {token}"
            yield request

    def sync_auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        raise RuntimeError("ClientCredentialsAuth is async-only; use it with httpx.AsyncClient.")

    async def _get_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            if (
                not force_refresh
                and self._access_token is not None
                and time.monotonic() < self._expires_at
            ):
                return self._access_token
            return await self._fetch_token()

    async def _fetch_token(self) -> str:
        data = {
            "grant_type": "client_credentials",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        if self._scopes:
            data["scope"] = " ".join(self._scopes)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=self._timeout_seconds
        ) as client:
            response = await client.post(self._token_url, data=data)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Client-credentials token request to {self._token_url} failed "
                f"with HTTP {response.status_code}."
            )
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError(
                f"Token response from {self._token_url} has no usable 'access_token'."
            )
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, int | float):
            lifetime = float(expires_in)
        else:
            lifetime = _DEFAULT_EXPIRES_IN_SECONDS
        self._expires_at = time.monotonic() + max(lifetime - _EXPIRY_SKEW_SECONDS, 0.0)
        self._access_token = token
        logger.debug("Fetched client-credentials token from %s", self._token_url)
        return token


def build_client_credentials(
    server: ServerRecord, *, transport: httpx.AsyncBaseTransport | None = None
) -> ClientCredentialsAuth:
    """Build a :class:`ClientCredentialsAuth` from *server*'s registry fields.

    The ``oauth_client_secret`` secret reference (``${env:VAR}`` / ``${file:path}``)
    is resolved here, at build time — the plaintext secret only ever exists in the
    auth provider's memory.
    """
    if not server.oauth_token_url or not server.oauth_client_id or not server.oauth_client_secret:
        raise ValueError(
            f"Server {server.name!r} uses auth='oauth_client_credentials' but is missing "
            f"oauth_token_url, oauth_client_id, or oauth_client_secret."
        )
    secret = resolve_secret_refs(server.oauth_client_secret)
    return ClientCredentialsAuth(
        token_url=server.oauth_token_url,
        client_id=server.oauth_client_id,
        client_secret=secret,
        scopes=server.oauth_scopes or None,
        transport=transport,
    )


def client_credentials_hook() -> ConnectHook:
    """Return a ``pre_mcp_connect`` hook attaching client-credentials auth where configured.

    The hook keeps one :class:`ClientCredentialsAuth` per server configuration so the
    in-memory token cache survives reconnects — without it every new upstream session
    would hit the token endpoint. Changing a server's credentials in the registry
    yields a new cache key, so stale providers are dropped on the next connect.
    """
    providers: dict[tuple[str, ...], ClientCredentialsAuth] = {}

    async def attach(ctx: ConnectContext) -> ConnectSettings | None:
        server = ctx.server
        if server.auth is not ServerAuth.OAUTH_CLIENT_CREDENTIALS:
            return None
        key = (
            server.id,
            server.oauth_token_url or "",
            server.oauth_client_id or "",
            server.oauth_client_secret or "",
            *server.oauth_scopes,
        )
        auth = providers.get(key)
        if auth is None:
            for stale in [k for k in providers if k[0] == server.id]:
                del providers[stale]
            auth = build_client_credentials(server)
            providers[key] = auth
        return ConnectSettings(auth=auth)

    return attach
