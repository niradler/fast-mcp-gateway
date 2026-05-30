"""Pydantic schemas shared across the gateway.

These describe the persisted registry (servers, groups) and the request/response
shapes used by the admin API. Storage backends in ``mcp_gateway.store`` translate
between these models and their own representation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Transport(StrEnum):
    """Transport used to reach an upstream MCP server. The gateway proxies remote
    servers only — local stdio subprocesses are out of scope."""

    HTTP = "http"
    SSE = "sse"


class ServerBase(BaseModel):
    """Fields common to creating and reading a server record."""

    name: str = Field(description="Unique name; also used as the mount namespace/prefix.")
    transport: Transport = Field(default=Transport.HTTP)
    url: str = Field(description="Endpoint URL for the upstream MCP server.")
    static_headers: dict[str, str] = Field(
        default_factory=dict,
        description="Headers always sent upstream; merged under hook-provided headers.",
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


class ServerCreate(ServerBase):
    """Payload to register a new upstream server."""


class ServerPatch(BaseModel):
    """Partial update for an existing server; unset fields are left unchanged."""

    name: str | None = None
    transport: Transport | None = None
    url: str | None = None
    static_headers: dict[str, str] | None = None
    allow: list[str] | None = None
    deny: list[str] | None = None
    timeout_seconds: float | None = Field(default=None, gt=0)
    enabled: bool | None = None
    tags: list[str] | None = None


class ServerRecord(ServerBase):
    """A persisted server, as returned by the store and admin API."""

    id: str


class GroupBase(BaseModel):
    """Fields common to creating and reading a group."""

    name: str
    member_server_ids: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list, description="Group-level allow overrides.")
    deny: list[str] = Field(default_factory=list, description="Group-level deny overrides.")


class GroupCreate(GroupBase):
    """Payload to create a group."""


class GroupPatch(BaseModel):
    """Partial update for a group; unset fields are left unchanged."""

    name: str | None = None
    member_server_ids: list[str] | None = None
    allow: list[str] | None = None
    deny: list[str] | None = None


class GroupRecord(GroupBase):
    """A persisted group, as returned by the store and admin API."""

    id: str


class CatalogTool(BaseModel):
    """One upstream tool, as captured in the persisted catalog snapshot.

    The snapshot is rebuilt on every ``GatewayBuilder.reload`` by introspecting the
    enabled upstreams. It is the single source of truth for both the gateway's
    ``tools/list`` and the ``search_tools`` / ``describe_tool`` meta-tools, so it
    carries enough of each tool's schema to reconstruct the MCP wire form without
    re-querying upstreams.

    ``name`` is the namespaced name as exposed by the gateway (``"<namespace>_<bare>"``);
    ``bare_name`` is the unprefixed name on the upstream.
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
