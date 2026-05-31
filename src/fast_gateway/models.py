"""Pydantic schemas shared across the gateway.

These describe the persisted registry (servers, groups) and the request/response
shapes used by the admin API. Storage backends in ``fast_gateway.store`` translate
between these models and their own representation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

_SERVER_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9-]*$"
_GROUP_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]*$"


class Transport(StrEnum):
    """Transport used to reach an upstream MCP server. The gateway proxies remote
    servers only - local stdio subprocesses are out of scope."""

    HTTP = "http"
    SSE = "sse"


class ServerAuth(StrEnum):
    """Upstream authentication scheme used when connecting to a registered server."""

    NONE = "none"
    OAUTH = "oauth"


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
            "Non-secret headers always sent upstream; merged under hook-provided headers. "
            "Stored in plaintext and returned by the admin read API — keep real credentials "
            "out of here and inject them from a pre_mcp_connect hook instead."
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
