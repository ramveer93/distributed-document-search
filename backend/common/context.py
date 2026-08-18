"""Per-request identity, resolved once by middleware and read everywhere else.

Nothing downstream re-parses a token or reads a raw header — they read this.
"""
from dataclasses import dataclass, field

from flask import g


@dataclass
class RequestContext:
    request_id: str
    tenant: str | None = None          # namespace, e.g. "acme" — immutable
    tenant_id: str | None = None       # uuid, for foreign keys
    user_id: str | None = None
    session_id: str | None = None
    rate_limit_rpm: int = 0
    extra: dict = field(default_factory=dict)


def set_context(ctx: RequestContext) -> None:
    g.ctx = ctx


def context() -> RequestContext:
    return g.ctx
