"""Pydantic schemas shared across the gateway.

These describe the persisted registry (servers, groups) and the request/response
shapes used by the admin API. Storage backends in ``mcp_gateway.store`` translate
between these models and their own representation.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Transport(StrEnum):
    """Transport used to reach an upstream MCP server."""

    HTTP = "http"
    SSE = "sse"
    STDIO = "stdio"


class ServerBase(BaseModel):
    """Fields common to creating and reading a server record."""

    name: str = Field(description="Unique name; also used as the mount namespace/prefix.")
    transport: Transport = Field(default=Transport.HTTP)
    url: str | None = Field(default=None, description="Endpoint URL for http/sse transports.")
    command: list[str] | None = Field(
        default=None, description="Argv for the stdio transport, e.g. ['npx', 'server']."
    )
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
    command: list[str] | None = None
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


class GroupRecord(GroupBase):
    """A persisted group, as returned by the store and admin API."""

    id: str
