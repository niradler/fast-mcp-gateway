"""Microsoft agent-governance-toolkit (agent-os) policy enforcement as a gateway plugin.

Experimental. Requires the optional ``agt`` extra, whose dependency is sourced from git,
so install it from a uv project (``uv add "fast-mcp-gateway[agt]"``) — plain ``pip`` cannot
resolve it until ``agent-os-kernel`` (being consolidated into ``agent-governance-toolkit-core``)
is published to PyPI.
"""

from fast_mcp_gateway.plugins.agentos.plugin import AgtAgentOsPlugin
from fast_mcp_gateway.plugins.agentos.settings import AgtAgentOsSettings

__all__ = ["AgtAgentOsPlugin", "AgtAgentOsSettings"]
