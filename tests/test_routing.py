"""Unit tests for the group-scoped dispatch shim (Milestone 3C).

These exercise the shim's contract in isolation — path popping, ContextVar
set/reset, scope rewriting, and lifespan no-op — with a fake downstream ASGI
app.  Full-stack propagation into the hook middleware is proven by the live
``.claude/scripts/m3c_e2e.py`` end-to-end run.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from fast_gateway.access import current_group
from fast_gateway.routing import GroupDispatch, split_group_path


def test_split_group_path_trailing_slash() -> None:
    assert split_group_path("/analytics/", "/") == ("analytics", "/")


def test_split_group_path_no_trailing_slash() -> None:
    assert split_group_path("/analytics", "/") == ("analytics", "/")


def test_split_group_path_subpath_preserved() -> None:
    assert split_group_path("/analytics/messages", "/") == ("analytics", "/messages")


def test_split_group_path_empty_is_unknown_group() -> None:
    assert split_group_path("/", "/") == ("", "/")
    assert split_group_path("", "/") == ("", "/")


def test_split_group_path_custom_transport_root() -> None:
    assert split_group_path("/analytics", "/mcp") == ("analytics", "/mcp")
    assert split_group_path("/analytics/", "/mcp") == ("analytics", "/mcp")


class _Recorder:
    """Fake downstream ASGI app: records the group seen and the resolved route.

    ``route_path`` mirrors Starlette's ``get_route_path``: the path the downstream
    router actually matches, i.e. ``scope['path']`` with ``scope['root_path']``
    stripped.
    """

    def __init__(self) -> None:
        self.seen_group: str | None = "<unset>"
        self.seen_root: str | None = None
        self.route_path: str | None = None

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.seen_group = current_group.get()
        self.seen_root = scope["root_path"]
        self.route_path = scope["path"][len(scope["root_path"]) :]


async def test_dispatch_sets_group_and_folds_segment_into_root_path() -> None:
    """The group segment is folded into root_path so the route resolves to root."""
    recorder = _Recorder()
    shim = GroupDispatch(recorder, transport_path="/")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_: MutableMapping[str, Any]) -> None:
        return None

    # Mirrors what Starlette's Mount("/mcp/g") passes: full path + root_path prefix.
    scope = {"type": "http", "path": "/mcp/g/analytics/", "root_path": "/mcp/g"}
    await shim(scope, receive, send)

    assert recorder.seen_group == "analytics"
    assert recorder.seen_root == "/mcp/g/analytics"
    assert recorder.route_path == "/"


async def test_dispatch_resolves_root_without_trailing_slash() -> None:
    """A group endpoint hit without a trailing slash still resolves to root."""
    recorder = _Recorder()
    shim = GroupDispatch(recorder, transport_path="/")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_: MutableMapping[str, Any]) -> None:
        return None

    scope = {"type": "http", "path": "/mcp/g/analytics", "root_path": "/mcp/g"}
    await shim(scope, receive, send)

    assert recorder.seen_group == "analytics"
    assert recorder.route_path == "/"


async def test_dispatch_resets_group_after_request() -> None:
    """The ContextVar must return to its prior value once the call completes."""
    recorder = _Recorder()
    shim = GroupDispatch(recorder, transport_path="/")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_: MutableMapping[str, Any]) -> None:
        return None

    assert current_group.get() is None
    await shim({"type": "http", "path": "/mcp/g/team-a", "root_path": "/mcp/g"}, receive, send)
    assert current_group.get() is None


async def test_dispatch_does_not_mutate_original_scope() -> None:
    recorder = _Recorder()
    shim = GroupDispatch(recorder, transport_path="/")

    async def receive() -> dict[str, Any]:
        return {"type": "http.request"}

    async def send(_: MutableMapping[str, Any]) -> None:
        return None

    scope = {"type": "http", "path": "/mcp/g/analytics/", "root_path": "/mcp/g"}
    await shim(scope, receive, send)
    assert scope["path"] == "/mcp/g/analytics/"
    assert scope["root_path"] == "/mcp/g"


async def test_dispatch_lifespan_is_noop() -> None:
    """The shim answers the lifespan protocol without touching the MCP app."""
    called = False

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        nonlocal called
        called = True

    shim = GroupDispatch(downstream, transport_path="/")

    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await shim({"type": "lifespan"}, receive, send)

    assert called is False
    assert {"type": "lifespan.startup.complete"} in sent
    assert {"type": "lifespan.shutdown.complete"} in sent
