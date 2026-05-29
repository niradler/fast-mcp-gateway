"""Group-scoped MCP endpoint dispatch (Milestone 3C).

The gateway exposes the full catalog at ``{mcp_path}`` and, alongside it, a
per-group view at ``{mcp_path}/g/{group}``.  Both serve the *same* shared MCP
ASGI app — there is no per-group proxy duplication.  Scoping is achieved with a
thin ASGI shim that sets the :data:`mcp_gateway.access.current_group` ContextVar
for the duration of the request; :class:`mcp_gateway.hooks.HookMiddleware` reads
that variable to filter the catalog and block out-of-scope/denied calls.

Lifespan ownership: the MCP app's lifespan runs exactly once, via
``gateway.lifespan`` on the host FastAPI app.  Starlette ``Mount`` does not
forward ``lifespan`` scopes to mounted apps, so this shim never triggers a
second run — but it answers the lifespan protocol defensively as a no-op in case
it is ever invoked directly.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from mcp_gateway.access import current_group


def split_group_path(remainder: str, transport_path: str) -> tuple[str, str]:
    """Split the post-mount remainder into ``(group, transport_subpath)``.

    ``remainder`` is the request path with the ``{mcp_path}/g`` mount prefix
    removed (i.e. ``scope['path']`` minus ``scope['root_path']``): ``/{group}``,
    ``/{group}/...``, or ``/`` / ``""``.  The leading segment is the group; what
    follows maps onto the shared MCP app.  When nothing follows (the common case
    of hitting the transport root, with or without a trailing slash) the subpath
    is ``transport_path`` so it lands on the same route the full ``{mcp_path}``
    mount would.

    An empty remainder (no group segment) yields ``("", transport_path)``; the
    empty group is treated as unknown and the policy renders an empty view.
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

    Modern Starlette ``Mount`` does not strip the matched prefix from
    ``scope['path']`` — it records the prefix in ``scope['root_path']`` and the
    downstream router derives its route as ``path`` minus ``root_path``.  This shim
    follows the same convention: it reads the leading ``{group}`` segment from the
    remainder, folds it into ``root_path`` (so the shared MCP app's router resolves
    to the transport root), sets ``current_group`` for the duration of the request,
    and resets it once the response completes.
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
