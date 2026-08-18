"""Cross-cutting request handling every service shares.

The gateway mints the request id and verifies the token first; services
verify it again here rather than trusting an injected header, so identity
cannot be forged by reaching a service directly.
"""
import time
import uuid

from flask import g, request

from . import auth
from .constants import H_REQUEST, H_SESSION
from .context import RequestContext, set_context
from .exceptions import Forbidden, Unauthorized


def install(app, logger, service: str, jwks_url: str, tenant_lookup,
            public_paths=("/health", "/health/detail", "/metrics")):
    """tenant_lookup(namespace) -> row | None

    Injected rather than imported so the gateway (which owns the users
    table) and the services (which only need tenant state) can supply
    different implementations.
    """

    @app.before_request
    def _before():
        g.started = time.perf_counter()
        g.request_id = request.headers.get(H_REQUEST) or f"r-{uuid.uuid4().hex[:12]}"

        if request.path in public_paths:
            set_context(RequestContext(request_id=g.request_id))
            return

        claims = auth.verify(auth.bearer_token(request.headers), jwks_url)
        tenant, user_id, sid = auth.claims_to_identity(claims)

        # a token proves identity, not current authorisation state: it stays
        # valid after the tenant is suspended, so we still read the row
        row = tenant_lookup(tenant)
        if not row:
            raise Unauthorized("unknown tenant")
        if row["status"] != "ACTIVE":
            raise Forbidden(f"tenant is {row['status'].lower()}")

        set_context(RequestContext(
            request_id=g.request_id,
            tenant=tenant,
            tenant_id=str(row["tenant_id"]),
            user_id=user_id,
            session_id=sid or request.headers.get(H_SESSION),
            rate_limit_rpm=row["rate_limit_rpm"],
        ))

    @app.after_request
    def _after(response):
        took = int((time.perf_counter() - getattr(g, "started", 0)) * 1000)
        response.headers[H_REQUEST] = getattr(g, "request_id", "-")
        ctx = getattr(g, "ctx", None)
        logger.info(f"{request.method} {request.path}", extra={
            "request_id": getattr(g, "request_id", "-"),
            "tenant": getattr(ctx, "tenant", None),
            "user_id": getattr(ctx, "user_id", None),
            "status": response.status_code,
            "took_ms": took,
        })
        return response
