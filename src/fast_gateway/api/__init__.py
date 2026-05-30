"""FastAPI admin routers for the gateway registry."""

from fast_gateway.api.groups import build_groups_router
from fast_gateway.api.servers import build_servers_router

__all__ = ["build_groups_router", "build_servers_router"]
