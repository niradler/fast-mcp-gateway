"""Optional integrations that adapt external governance engines to the gateway.

Each integration is delivered as a :class:`mcp_gateway.plugins.Plugin` and depends on
an optional extra, so the core gateway never imports it unless the user opts in.
"""
