"""Microsoft agent-governance-toolkit (agent-os) policy enforcement as a gateway plugin.

Requires the optional ``agt`` extra (``pip install fast-mcp-gateway[agt]``).
"""

from mcp_gateway.integrations.agt.plugin import AgtAgentOsPlugin
from mcp_gateway.integrations.agt.settings import AgtSettings

__all__ = ["AgtAgentOsPlugin", "AgtSettings"]
