"""Secret references: ``${env:NAME}`` / ``${file:path}`` placeholders in registry values.

The registry (and the admin API that echoes it) must never hold a credential in
plaintext, so values embed references resolved at connect time instead. Only the
reference is persisted; the secret exists in memory for the connection's lifetime.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_SECRET_REF = re.compile(r"\$\{(env|file):([^}]+)\}")


class SecretResolutionError(ValueError):
    """A ``${env:...}`` / ``${file:...}`` reference could not be resolved."""


def contains_secret_ref(value: str) -> bool:
    """Return True when *value* embeds at least one secret reference."""
    return _SECRET_REF.search(value) is not None


def resolve_secret_refs(value: str) -> str:
    """Substitute every secret reference in *value*, returning the resolved string.

    Values without references pass through unchanged. Raises
    :class:`SecretResolutionError` when an environment variable is unset or a
    file is missing/unreadable, so a misconfigured server fails loudly at connect
    time instead of silently sending a literal placeholder upstream.
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
