"""Gateway — the only exposed port.

Owns the cross-cutting concerns: authentication, the request id that flows
through every service and into the queue, and per-tenant rate limiting.
It does NOT own the trust boundary — it forwards the original Authorization
header and each service verifies it independently.
"""
import time
import uuid

import jwt
from flask import Flask, g, request

from common import health, problem
from common.clients import redis_client
from common.config import settings
from common.context import RequestContext, set_context
from common.db import bootstrap, engine, ping as pg_ping
from common.exceptions import Forbidden, Unauthorized
from common.logging_setup import configure
from common.observability import (collectors, install_http_metrics,
                                  metrics_bp)

from .managers import keys, rate_limit
from .repositories import users
from .routes import auth as auth_routes
from .routes import proxy as proxy_routes

SERVICE = "gateway"
PUBLIC_PATHS = {"/auth/token", "/.well-known/jwks.json",
                "/health", "/health/detail", "/metrics"}


def create_app() -> Flask:
    logger = configure(SERVICE, settings().log_level)
    app = Flask(SERVICE)

    bootstrap.run()
    keys.private_key()          # generate the keypair before serving traffic
    # gunicorn --preload builds the app in the master and then forks, so
    # drop the pool here: children must open their own connections
    # rather than inherit the parent's sockets.
    engine().dispose()
    logger.info("gateway ready", extra={"request_id": "-"})

    @app.before_request
    def _before():
        g.started = time.perf_counter()
        g.request_id = (request.headers.get("X-Request-Id")
                        or f"r-{uuid.uuid4().hex[:12]}")
        g.rate_remaining = None

        if request.path in PUBLIC_PATHS:
            set_context(RequestContext(request_id=g.request_id))
            return

        raw = request.headers.get("Authorization", "")
        if not raw.startswith("Bearer "):
            raise Unauthorized("missing bearer token")
        s = settings()
        try:
            claims = jwt.decode(raw[7:], keys.public_key(), algorithms=["RS256"],
                                audience=s.jwt_audience, issuer=s.jwt_issuer)
        except jwt.ExpiredSignatureError:
            raise Unauthorized("token expired")
        except jwt.InvalidTokenError as exc:
            raise Unauthorized(f"invalid token: {exc}")

        tenant = claims.get("tenant")
        row = users.find_tenant(tenant) if tenant else None
        if not row:
            raise Unauthorized("unknown tenant")
        # a token proves identity, not current state: it stays valid after a
        # tenant is suspended, so the row is still checked on every request
        if row["status"] != "ACTIVE":
            raise Forbidden(f"tenant is {row['status'].lower()}")

        set_context(RequestContext(
            request_id=g.request_id,
            tenant=tenant,
            tenant_id=row["tenant_id"],
            user_id=claims.get("sub"),
            session_id=claims.get("sid"),
            rate_limit_rpm=row["rate_limit_rpm"],
        ))
        g.rate_remaining = rate_limit.check(tenant, row["rate_limit_rpm"])

    @app.after_request
    def _after(response):
        took = int((time.perf_counter() - getattr(g, "started", 0)) * 1000)
        response.headers["X-Request-Id"] = getattr(g, "request_id", "-")
        ctx = g.get("ctx")
        logger.info(f"{request.method} {request.path}", extra={
            "request_id": getattr(g, "request_id", "-"),
            "tenant": getattr(ctx, "tenant", None),
            "user_id": getattr(ctx, "user_id", None),
            "status": response.status_code,
            "took_ms": took,
        })
        return response

    install_http_metrics(app, SERVICE)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(health.blueprint(SERVICE, {
        "postgres": pg_ping,
        "redis": redis_client.ping,
    }))
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(proxy_routes.bp)   # catch-all, registered last
    problem.register_error_handlers(app, logger)
    return app


app = create_app()
