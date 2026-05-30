"""Group-scoped MCP endpoint dispatch.

Full catalog (``{mcp_path}``) and per-group view (``{mcp_path}/g/{group}``) route to
the same shared ASGI app. :class:`GroupDispatch` sets ``current_group`` per request;
:class:`HookMiddleware` reads it to filter and enforce access.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from fast_gateway.access import current_group


def split_group_path(remainder: str, transport_path: str) -> tuple[str, str]:
    """Extract ``(group, transport_subpath)`` from the post-mount path remainder.

    ``remainder`` is ``scope['path']`` minus the ``{mcp_path}/g`` prefix.
    When nothing follows the group segment, ``transport_path`` is used as the subpath
    so requests land on the same transport route as the full ``{mcp_path}`` mount.
    An empty remainder yields ``("", transport_path)``; empty group → empty (unknown) view.
    """
    stripped = remainder.lstrip("/")
    if not stripped:
        return "", transport_path
    group, _, rest = stripped.partition("/")
    if not rest:
        return group, transport_path
    return group, "/" + rest


class GroupDispatch:
    """ASGI shim mounted at ``{mcp_path}/g`` that scopes requests to a group.

    Reads the leading ``{group}`` segment from the path, folds it into
    ``scope['root_path']`` (so the shared MCP app resolves to the transport root),
    and sets ``current_group`` for the request duration.
    """

    def __init__(self, mcp_app: ASGIApp, transport_path: str) -> None:
        self._mcp_app = mcp_app
        self._transport_path = transport_path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
            return

        root_path = scope.get("root_path", "")
        remainder = scope.get("path", "")[len(root_path) :]
        group, rest = split_group_path(remainder, self._transport_path)

        new_root = f"{root_path}/{group}" if group else root_path
        new_path = new_root + rest

        scoped = dict(scope)
        scoped["root_path"] = new_root
        scoped["path"] = new_path
        scoped["raw_path"] = new_path.encode("ascii")

        token = current_group.set(group)
        try:
            await self._mcp_app(scoped, receive, send)
        finally:
            current_group.reset(token)

    @staticmethod
    async def _handle_lifespan(receive: Receive, send: Send) -> None:
        """No-op lifespan handler — the real lifespan is owned by the host app."""
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
