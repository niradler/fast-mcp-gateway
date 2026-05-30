"""Per-server and per-group allow/deny glob enforcement.

``AccessPolicy`` is the single authoritative object for deciding which tools
are visible and callable.  Rules are pre-compiled once per ``rebuild()`` call
so the per-request path is only dict lookups and ``fnmatch.fnmatchcase`` — no
I/O, no store access, no recompilation.

``current_group`` is a context variable that M3c will set per-request to scope
enforcement to a specific group.  In this milestone it is always ``None``.

Non-namespaced tools (names that do not match any registered server namespace)
are always allowed.  This covers gateway-local meta-tools such as
``search_tools`` that are not proxied from an upstream server.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from mcp_gateway.models import GroupRecord, ServerRecord

current_group: ContextVar[str | None] = ContextVar("mcp_gateway_current_group", default=None)


@dataclass
class _ServerRules:
    """Pre-compiled rules for one server namespace."""

    allow: list[str]
    deny: list[str]


@dataclass
class _GroupRules:
    """Pre-compiled rules for one group."""

    member_namespaces: frozenset[str]
    allow: list[str]
    deny: list[str]


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    """Return True if *name* matches at least one glob pattern in *patterns*."""
    return any(fnmatchcase(name, p) for p in patterns)


def _rule_allows(bare: str, allow: Sequence[str], deny: Sequence[str]) -> bool:
    """Evaluate server-level (or group-level) allow/deny rules.

    Semantics:
    - deny non-empty AND bare matches any deny pattern → DENIED.
    - allow non-empty AND bare matches NO allow pattern → DENIED.
    - Otherwise → ALLOWED.

    Deny always wins.  Empty allow means "allow everything".
    """
    if deny and _matches_any(bare, deny):
        return False
    return not (allow and not _matches_any(bare, allow))


class AccessPolicy:
    """Compiled allow/deny policy over a snapshot of the server/group registry.

    Call ``rebuild()`` after every ``GatewayBuilder.reload()`` to refresh rules.
    All read methods (``allows``, ``filter_tools``, ``split_namespace``) are
    safe to call concurrently — they only read immutable state set by ``rebuild``.
    """

    def __init__(self) -> None:
        self._server_rules: dict[str, _ServerRules] = {}
        self._group_rules: dict[str, _GroupRules] = {}
        # Sorted longest-first for correct longest-prefix matching.
        self._namespaces_by_len: list[str] = []

    def rebuild(self, servers: Sequence[ServerRecord], groups: Sequence[GroupRecord]) -> None:
        """Recompile all rules from a registry snapshot.

        Must be called with the *full* server list (not just enabled ones) so
        that ``split_namespace`` can recognise every registered namespace.
        """
        server_by_id: dict[str, str] = {}  # id → name (namespace)
        server_rules: dict[str, _ServerRules] = {}

        for srv in servers:
            server_rules[srv.name] = _ServerRules(allow=list(srv.allow), deny=list(srv.deny))
            server_by_id[srv.id] = srv.name

        group_rules: dict[str, _GroupRules] = {}
        for grp in groups:
            member_namespaces = frozenset(
                server_by_id[sid] for sid in grp.member_server_ids if sid in server_by_id
            )
            group_rules[grp.name] = _GroupRules(
                member_namespaces=member_namespaces,
                allow=list(grp.allow),
                deny=list(grp.deny),
            )

        # Longest first so "math2" is tried before "math" for "math2_add".
        namespaces_by_len = sorted(server_rules.keys(), key=len, reverse=True)

        # Atomic swap — readers see either the old or the new complete state.
        self._server_rules = server_rules
        self._group_rules = group_rules
        self._namespaces_by_len = namespaces_by_len

    def split_namespace(self, tool_name: str) -> tuple[str | None, str]:
        """Split a (potentially namespaced) tool name into (namespace, bare).

        Uses longest-prefix matching against known server namespaces.  The
        separator is a single underscore; the namespace must be followed by
        ``_<something>`` — a tool whose name exactly equals a namespace string
        is treated as non-namespaced.

        Returns ``(None, tool_name)`` for non-namespaced tools.
        """
        for ns in self._namespaces_by_len:
            prefix = ns + "_"
            if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
                return ns, tool_name[len(prefix) :]
        return None, tool_name

    def allows(self, tool_name: str, group: str | None = None) -> bool:
        """Return True if *tool_name* is allowed, optionally scoped to *group*.

        Non-namespaced tools (no matching server namespace) are always allowed.

        When *group* is provided:
        - Unknown group → False (empty view).
        - Tool's namespace not in group's member set → False.
        - Group's own allow/deny applied on top of (not instead of) server rules.
        """
        ns, bare = self.split_namespace(tool_name)

        if ns is None:
            # Non-namespaced tool (e.g. gateway meta-tool) — always visible.
            return True

        server_rule = self._server_rules.get(ns)
        if server_rule is None:
            # Defensive: a namespace from split_namespace is always in server_rules.
            return True

        if not _rule_allows(bare, server_rule.allow, server_rule.deny):
            return False

        if group is None:
            return True

        grp_rule = self._group_rules.get(group)
        if grp_rule is None:
            return False

        if ns not in grp_rule.member_namespaces:
            return False

        return _rule_allows(bare, grp_rule.allow, grp_rule.deny)

    def filter_tools(self, tools: Sequence[Any], group: str | None = None) -> list[Any]:
        """Return only the tools the policy allows.

        *tools* must have a ``.name`` attribute (matches the FastMCP ``Tool``
        type as well as duck-typed stand-ins used in tests).
        """
        return [t for t in tools if self.allows(t.name, group)]
