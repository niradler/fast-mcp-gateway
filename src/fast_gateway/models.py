"""Pydantic schemas shared across the gateway.

These describe the persisted registry (servers, groups) and the request/response
shapes used by the admin API. Storage backends in ``fast_gateway.store`` translate
between these models and their own representation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from fast_gateway.secret_refs import contains_secret_ref

_SERVER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]*$"
_GROUP_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class Transport(StrEnum):
    """Transport used to reach an upstream MCP server. The gateway proxies remote
    servers only - local stdio subprocesses are out of scope."""

    HTTP = "http"
    SSE = "sse"


class ServerAuth(StrEnum):
    """Upstream authentication scheme used when connecting to a registered server.

    ``oauth`` is the interactive browser authorization-code flow (Mode B only);
    ``oauth_client_credentials`` is the headless machine-to-machine grant. Static
    token / API-key auth needs no scheme: put a ``${env:}``/``${file:}`` secret
    reference in ``static_headers`` and it resolves at connect time.
    """

    NONE = "none"
    OAUTH = "oauth"
    OAUTH_CLIENT_CREDENTIALS = "oauth_client_credentials"


class ServerBase(BaseModel):
    """Fields common to creating and reading a server record."""

    name: str = Field(
        min_length=1,
        pattern=_SERVER_NAME_PATTERN,
        description=(
            "Unique name; also used as the mount namespace/prefix. Letters, digits and "
            "hyphens only — no underscore (it is the namespace/tool-name separator)."
        ),
    )
    transport: Transport = Field(default=Transport.HTTP)
    url: str = Field(description="Endpoint URL for the upstream MCP server.")
    static_headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Headers always sent upstream; merged under hook-provided headers. Values may "
            "embed ${env:VAR} / ${file:path} secret references resolved at connect time — "
            "use those for credentials (e.g. 'Bearer ${env:API_TOKEN}'), because this field "
            "is stored in plaintext and returned by the admin read API."
        ),
    )
    allow: list[str] = Field(
        default_factory=list, description="Glob patterns of tool names to expose (empty = all)."
    )
    deny: list[str] = Field(
        default_factory=list, description="Glob patterns of tool names to hide (wins over allow)."
    )
    timeout_seconds: float = Field(default=30.0, gt=0)
    enabled: bool = Field(default=True)
    tags: list[str] = Field(default_factory=list)
    auth: ServerAuth = Field(
        default=ServerAuth.NONE,
        description=(
            "Upstream auth scheme. 'oauth' runs FastMCP's browser OAuth flow on first "
            "connect and caches tokens persistently."
        ),
    )
    oauth_scopes: list[str] = Field(
        default_factory=list,
        description="OAuth scopes to request; empty = server/registration default.",
    )
    oauth_token_url: str | None = Field(
        default=None,
        description="Token endpoint for the client_credentials grant.",
    )
    oauth_client_id: str | None = Field(
        default=None,
        description="OAuth client id for the client_credentials grant (not secret).",
    )
    oauth_client_secret: str | None = Field(
        default=None,
        description=(
            "OAuth client secret as a ${env:VAR} or ${file:path} reference, resolved at "
            "connect time. Raw secrets are rejected so the registry never stores one."
        ),
    )

    @model_validator(mode="after")
    def _validate_client_credentials(self) -> ServerBase:
        if self.auth is not ServerAuth.OAUTH_CLIENT_CREDENTIALS:
            return self
        required = ("oauth_token_url", "oauth_client_id", "oauth_client_secret")
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(
                f"auth='oauth_client_credentials' requires {', '.join(missing)} to be set."
            )
        if self.oauth_client_secret is not None and not contains_secret_ref(
            self.oauth_client_secret
        ):
            raise ValueError(
                "oauth_client_secret must be a ${env:VAR} or ${file:path} reference; "
                "raw secrets are not stored in the registry."
            )
        return self


class ServerCreate(ServerBase):
    """Payload to register a new upstream server."""


class ServerPatch(BaseModel):
    """Partial update for an existing server; unset fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1, pattern=_SERVER_NAME_PATTERN)
    transport: Transport | None = None
    url: str | None = None
    static_headers: dict[str, str] | None = None
    allow: list[str] | None = None
    deny: list[str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None
    tags: list[str] | None = None
    auth: ServerAuth | None = None
    oauth_scopes: list[str] | None = None
    oauth_token_url: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None


class ServerRecord(ServerBase):
    """A persisted server, as returned by the store and admin API."""

    id: str


class GroupBase(BaseModel):
    """Fields common to creating and reading a group."""

    name: str = Field(
        min_length=1,
        pattern=_GROUP_NAME_PATTERN,
        description="Unique group name; used in the /mcp/g/{group} route and as the policy key.",
    )
    member_server_ids: list[str] = Field(default_factory=list)
    allow: list[str] = Field(
        default_factory=list,
        description=(
            "Group-level allow globs, applied on top of each member server's rules "
            "(empty = inherit server). Can only narrow: a tool the owning server denies "
            "stays denied even if a group allow matches it."
        ),
    )
    deny: list[str] = Field(
        default_factory=list,
        description="Group-level deny globs, layered on top of server rules; deny wins.",
    )


class GroupCreate(GroupBase):
    """Payload to create a group."""


class GroupPatch(BaseModel):
    """Partial update for a group; unset fields are left unchanged."""

    name: str | None = Field(default=None, min_length=1, pattern=_GROUP_NAME_PATTERN)
    member_server_ids: list[str] | None = None
    allow: list[str] | None = None
    deny: list[str] | None = None


class GroupRecord(GroupBase):
    """A persisted group, as returned by the store and admin API."""

    id: str


class CatalogTool(BaseModel):
    """One upstream tool in the persisted catalog snapshot.

    ``name`` is the gateway-exposed namespaced name (``"<namespace>_<bare>"``);
    ``bare_name`` is the unprefixed name on the upstream. Carries enough schema to
    reconstruct the MCP wire form without re-querying upstreams.
    """

    server_id: str
    namespace: str = Field(description="Owning server's name; the mount namespace.")
    name: str = Field(description="Namespaced tool name as exposed by the gateway.")
    bare_name: str = Field(description="Unprefixed tool name on the upstream server.")
    title: str | None = None
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(
        default_factory=dict, description="JSON schema for the tool's input."
    )
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
