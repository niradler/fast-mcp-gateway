"""Unit tests for AccessPolicy — glob-based allow/deny enforcement.

No FastMCP required: ServerRecord/GroupRecord are built directly, and tool
stand-ins use SimpleNamespace(name=...).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from fast_gateway.access import AccessPolicy
from fast_gateway.models import GroupRecord, ServerRecord


def make_server(
    name: str,
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
    enabled: bool = True,
) -> ServerRecord:
    return ServerRecord(
        id=name,
        name=name,
        url=f"https://{name}.example.com/mcp",
        allow=allow or [],
        deny=deny or [],
        enabled=enabled,
    )


def make_group(
    name: str,
    member_server_ids: list[str],
    *,
    allow: list[str] | None = None,
    deny: list[str] | None = None,
) -> GroupRecord:
    return GroupRecord(
        id=name,
        name=name,
        member_server_ids=member_server_ids,
        allow=allow or [],
        deny=deny or [],
    )


def tool(name: str) -> Any:
    return SimpleNamespace(name=name)


# ---------------------------------------------------------------------------
# split_namespace
# ---------------------------------------------------------------------------


def test_split_namespace_known() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github")], [])
    assert policy.split_namespace("github_get_repo") == ("github", "get_repo")


def test_split_namespace_unknown_returns_none() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github")], [])
    ns, bare = policy.split_namespace("unknown_tool")
    assert ns is None
    assert bare == "unknown_tool"


def test_split_namespace_longest_prefix() -> None:
    """'math2_add' must split to ('math2', 'add'), not ('math', '2_add')."""
    policy = AccessPolicy()
    policy.rebuild([make_server("math"), make_server("math2")], [])
    assert policy.split_namespace("math2_add") == ("math2", "add")
    assert policy.split_namespace("math_add") == ("math", "add")


def test_split_namespace_exact_match_no_underscore() -> None:
    """A tool whose name IS the namespace (no suffix) is non-namespaced."""
    policy = AccessPolicy()
    policy.rebuild([make_server("github")], [])
    ns, _bare = policy.split_namespace("github")
    # "github" with no trailing "_<bare>" cannot be split — treated as non-namespaced
    assert ns is None


def test_split_namespace_double_underscore_in_bare_name() -> None:
    """A bare tool name containing '__' must split only at the namespace boundary.

    FastMCP joins namespace + tool with a single underscore, but the bare tool
    name itself may contain underscores or double underscores. Longest-prefix
    matching against known namespaces splits at the first separator that
    completes a registered namespace, leaving the rest (including any '__')
    intact — it does NOT split on an arbitrary '__'.
    """
    policy = AccessPolicy()
    policy.rebuild([make_server("math")], [])
    assert policy.split_namespace("math_get__user") == ("math", "get__user")
    assert policy.split_namespace("math__add") == ("math", "_add")


def test_double_underscore_bare_name_allow_deny() -> None:
    """Allow/deny globs apply to the bare name even when it contains '__'."""
    policy = AccessPolicy()
    policy.rebuild([make_server("math", deny=["get__*"])], [])
    assert policy.allows("math_get__user") is False
    assert policy.allows("math_add") is True


# ---------------------------------------------------------------------------
# allows — server-level rules
# ---------------------------------------------------------------------------


def test_empty_rules_allows_all() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github")], [])
    assert policy.allows("github_get_repo") is True
    assert policy.allows("github_delete_repo") is True


def test_allow_list_narrows() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github", allow=["get_*"])], [])
    assert policy.allows("github_get_repo") is True
    assert policy.allows("github_delete_repo") is False


def test_deny_list_blocks() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github", deny=["delete_*"])], [])
    assert policy.allows("github_delete_repo") is False
    assert policy.allows("github_get_repo") is True


def test_deny_overrides_allow() -> None:
    """deny wins even when the bare name also matches an allow pattern."""
    policy = AccessPolicy()
    policy.rebuild([make_server("github", allow=["*"], deny=["delete_*"])], [])
    assert policy.allows("github_delete_repo") is False
    assert policy.allows("github_get_repo") is True


def test_exact_glob_match() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("svc", allow=["run_job"])], [])
    assert policy.allows("svc_run_job") is True
    assert policy.allows("svc_run_job_v2") is False


def test_suffix_glob() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("svc", deny=["*_internal"])], [])
    assert policy.allows("svc_list_internal") is False
    assert policy.allows("svc_list") is True


def test_non_namespaced_tool_always_allowed() -> None:
    """Tools whose name doesn't match any registered server namespace pass through."""
    policy = AccessPolicy()
    policy.rebuild([make_server("github", allow=["get_*"])], [])
    # "search_tools" has no known namespace — must be allowed regardless
    assert policy.allows("search_tools") is True
    assert policy.allows("describe_tool") is True


# ---------------------------------------------------------------------------
# filter_tools
# ---------------------------------------------------------------------------


def test_filter_tools_removes_denied() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github", allow=["get_*"])], [])
    tools = [tool("github_get_repo"), tool("github_delete_repo"), tool("search_tools")]
    result = policy.filter_tools(tools)
    names = [t.name for t in result]
    assert "github_get_repo" in names
    assert "github_delete_repo" not in names
    assert "search_tools" in names  # non-namespaced, always allowed


def test_filter_tools_empty_rules_passes_all() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("svc")], [])
    tools = [tool("svc_a"), tool("svc_b"), tool("meta")]
    assert len(policy.filter_tools(tools)) == 3


# ---------------------------------------------------------------------------
# Group scoping (M3c will add the routing shim; rules must be built now)
# ---------------------------------------------------------------------------


def test_group_restricts_to_member_servers() -> None:
    """A group that only includes 'github' must not expose 'gitlab_*' tools."""
    github = make_server("github")
    gitlab = make_server("gitlab")
    group = make_group("g1", member_server_ids=["github"])
    policy = AccessPolicy()
    policy.rebuild([github, gitlab], [group])

    assert policy.allows("github_get_repo", group="g1") is True
    assert policy.allows("gitlab_get_repo", group="g1") is False


def test_group_allow_narrows_within_member() -> None:
    github = make_server("github")
    group = make_group("g1", member_server_ids=["github"], allow=["get_*"])
    policy = AccessPolicy()
    policy.rebuild([github], [group])

    assert policy.allows("github_get_repo", group="g1") is True
    assert policy.allows("github_delete_repo", group="g1") is False


def test_group_deny_blocks_within_member() -> None:
    github = make_server("github")
    group = make_group("g1", member_server_ids=["github"], deny=["delete_*"])
    policy = AccessPolicy()
    policy.rebuild([github], [group])

    assert policy.allows("github_delete_repo", group="g1") is False
    assert policy.allows("github_get_repo", group="g1") is True


def test_unknown_group_returns_false() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github")], [])
    assert policy.allows("github_get_repo", group="nonexistent") is False


def test_no_group_falls_back_to_server_rules() -> None:
    policy = AccessPolicy()
    policy.rebuild([make_server("github", allow=["get_*"])], [])
    assert policy.allows("github_get_repo", group=None) is True
    assert policy.allows("github_delete_repo", group=None) is False
