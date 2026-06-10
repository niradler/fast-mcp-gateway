# agentos plugin (experimental)

Governs the gateway with Microsoft's
[agent-governance-toolkit](https://github.com/microsoft/agent-governance-toolkit)
(agent-os) policy engine, wired in as a single `Plugin`: `AgtAgentOsPlugin`.

> **Experimental.** The upstream dependency is not on PyPI yet, so this plugin installs
> only from inside a uv project (see [Install](#install)). The gateway and the rest of
> the plugin system work fully **without** it — only this integration needs the extra.

## What it does

The plugin's core, always-on capability is the **policy engine**: it evaluates agent-os
policy for every tool call — scoped to the active group — and denies calls the policy
rejects. Every other agent-os capability is an opt-in toggle on `AgtAgentOsSettings`,
each bound to the gateway hook seam where it belongs:

| Capability | Toggle | Seam | Effect |
| --- | --- | --- | --- |
| **Policy engine** | always on | `pre_tool_call` | deny tool calls the group's policy rejects |
| **Prompt injection** | `enable_prompt_injection` | `pre_tool_call` | deny calls whose arguments look like prompt injection |
| **Semantic policy** | `enable_semantic_policy` (+ `semantic_deny`) | `pre_tool_call` | deny calls whose classified intent is dangerous |
| **Response scan** | `enable_response_scan` | `post_tool_call` | block responses flagged unsafe (credential / PII / threat) |
| **Credential redaction** | `enable_credential_redaction` | `post_tool_call` | redact secrets / PII out of responses |
| **Egress policy** | `enable_egress_policy` (+ `egress_rules`) | `pre_mcp_connect` | refuse upstreams whose URL is outside the allowlist |
| **MCP security scan** | `enable_mcp_security_scan` | `pre_list_tools` | drop (or log) tools flagged for poisoning / hidden instructions |
| **Rate limiting** | `enable_rate_limiting` (+ `rate_limit_max_calls`, `rate_limit_window_seconds`) | `pre_tool_call` | deny tool calls that exceed the per-group sliding-window budget |

Where agent-os already defines a config type it is reused verbatim — `PolicyDocument`,
`DetectionConfig`, `IntentCategory`, `SemanticPolicyConfig`, `EgressRule` — so values
pass straight through with no re-modelling.

### Policy sources

The policy engine reads one of:

- `policy_dir` — a directory of YAML policy documents, validated at startup; or
- `policies` — a list of in-memory `PolicyDocument` objects.

`policy_dir` wins if both are set. With neither set the plugin **refuses to build**
unless `allow_no_policies=True` (explicit opt-in to allow-all mode). `fail_closed`
(default `True`) denies a tool call when policy evaluation itself raises — the safe
default for a governance component.

## Install

```bash
uv add "fast-gateway[agt]"   # from within a uv project — honors the git source
```

The `agt` extra is sourced from the agent-governance-toolkit GitHub monorepo (via uv
`[tool.uv.sources]`) until `agent-os-kernel` 4.x is published to PyPI. Because of that
git source, install it from within a uv project; a plain `pip install
"fast-gateway[agt]"` cannot resolve the dependency and will fail until it lands on
PyPI. Upstream, `agent-os-kernel` is being consolidated into
`agent-governance-toolkit-core`.

## Usage

```python
from fast_gateway import SqliteStore, create_gateway
from fast_gateway.plugins.agentos import AgtAgentOsPlugin, AgtAgentOsSettings

gateway = create_gateway(
    store=SqliteStore("gateway.db"),
    plugins=[
        AgtAgentOsPlugin(
            AgtAgentOsSettings(
                policy_dir="./policies",
                fail_closed=True,
                enable_prompt_injection=True,
                enable_response_scan=True,
                enable_credential_redaction=True,
            )
        )
    ],
)
```

The plugin's `setup` (policy load/validation) and `teardown` are driven from the gateway
lifespan, so a malformed policy document fails the app at startup rather than at the
first tool call.

For semantic policy, the built-in signals are only samples — supply a tuned
`semantic_config` (`SemanticPolicyConfig`) and the `semantic_deny` categories you want
enforced. See [`settings.py`](settings.py) for every field and its default.
