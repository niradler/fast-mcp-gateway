"""Secret & variable references in registry values, resolved at connect time.

``${env:NAME}`` reads a process env var, ``${file:path}`` a file's stripped contents,
and ``${var:NAME}`` a request-scoped runtime variable (see :func:`runtime_vars`) — the
latter injected per request, e.g. lifted from an incoming header. Only the reference is
persisted, never the secret, so the registry and admin read API never hold a credential.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_SECRET_REF = re.compile(r"\$\{(env|file|var):([^}]+)\}")

_EMPTY_VARS: Mapping[str, str] = {}

runtime_variables: ContextVar[Mapping[str, str]] = ContextVar(
    "fast_gateway_runtime_variables", default=_EMPTY_VARS
)
"""Request-scoped variables backing ``${var:NAME}`` references.

Bind it with :func:`runtime_vars`; read the current binding with
:func:`get_runtime_vars`. Defaults to an empty mapping outside any request.
"""


def get_runtime_vars() -> Mapping[str, str]:
    """Return the runtime variables bound in the current scope (empty if none)."""
    return runtime_variables.get()


@contextmanager
def runtime_vars(values: Mapping[str, str]) -> Iterator[None]:
    """Bind runtime variables for ``${var:NAME}`` resolution within the block.

    Values merge over any already-bound variables, so nested scopes are additive
    and an inner binding overrides an outer one of the same name. The previous
    mapping is restored on exit, making this safe to nest and to use per request.
    """
    merged: dict[str, str] = {**runtime_variables.get(), **values}
    token = runtime_variables.set(merged)
    try:
        yield
    finally:
        runtime_variables.reset(token)


class SecretResolutionError(ValueError):
    """A ``${env:...}`` / ``${file:...}`` / ``${var:...}`` reference could not be resolved."""


def contains_secret_ref(value: str) -> bool:
    """Return True when *value* embeds at least one secret reference."""
    return _SECRET_REF.search(value) is not None


def resolve_secret_refs(value: str) -> str:
    """Substitute every secret reference in *value*, returning the resolved string.

    Values without references pass through unchanged. Raises
    :class:`SecretResolutionError` when an env var is unset, a runtime variable is
    unbound, or a file is missing — so a misconfigured server fails loudly at connect
    time instead of sending a literal placeholder upstream.
    """

    def _substitute(match: re.Match[str]) -> str:
        kind, ref = match.group(1), match.group(2).strip()
        if kind == "env":
            resolved = os.environ.get(ref)
            if resolved is None:
                raise SecretResolutionError(
                    f"Secret ref '${{env:{ref}}}' cannot be resolved: "
                    f"environment variable {ref!r} is not set."
                )
            return resolved
        if kind == "var":
            runtime_value = runtime_variables.get().get(ref)
            if runtime_value is None:
                raise SecretResolutionError(
                    f"Runtime variable ref '${{var:{ref}}}' cannot be resolved: "
                    f"variable {ref!r} is not bound in the current request scope."
                )
            return runtime_value
        try:
            return Path(ref).expanduser().read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise SecretResolutionError(
                f"Secret ref '${{file:{ref}}}' cannot be resolved: {exc}"
            ) from exc

    return _SECRET_REF.sub(_substitute, value)


def resolve_header_refs(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with all secret references in values resolved."""
    return {name: resolve_secret_refs(value) for name, value in headers.items()}
