"""The API service.

search_service, document_service and index_service are separate packages —
separate concerns, separate layers — but one deployable. Ingest is roughly
a tenth of search traffic, so splitting them into three containers would buy
operational cost with no scaling benefit. The indexer is separate because it
is a queue consumer: it scales on lag, not on request rate.
"""
from flask import Flask

from common import health, middleware, problem
from common.clients import elastic, kafka_client, redis_client, s3
from common.config import settings
from common.db import bootstrap, engine, ping as pg_ping
from common.logging_setup import configure
from common.observability import install_http_metrics, metrics_bp
from document_service.routes import documents as document_routes
from index_service.routes import documents as index_routes
from search_service.routes import search as search_routes

SERVICE = "api"


def _tenant_lookup(namespace: str) -> dict | None:
    from sqlalchemy import select

    from common.db import Tenant, session
    with session() as s:
        t = s.scalar(select(Tenant).where(Tenant.namespace == namespace))
        if not t:
            return None
        return {"tenant_id": str(t.tenant_id), "namespace": t.namespace,
                "status": t.status, "rate_limit_rpm": t.rate_limit_rpm}


def create_app() -> Flask:
    logger = configure(SERVICE, settings().log_level)
    app = Flask(SERVICE)

    bootstrap.run()
    elastic.ensure_index()
    s3.ensure_bucket()
    # gunicorn --preload builds the app in the master and then forks, so
    # drop the pool here: children must open their own connections
    # rather than inherit the parent's sockets.
    engine().dispose()
    logger.info("api ready", extra={"request_id": "-"})

    # every service verifies the token itself rather than trusting a header
    # the gateway injected — reaching this service directly cannot forge a
    # tenant
    jwks_url = f"{settings().gateway_internal_url}/.well-known/jwks.json"
    middleware.install(app, logger, SERVICE, jwks_url, _tenant_lookup)

    install_http_metrics(app, SERVICE)
    app.register_blueprint(metrics_bp)
    app.register_blueprint(health.blueprint(SERVICE, {
        "postgres": pg_ping,
        "elasticsearch": elastic.ping,
        "redis": redis_client.ping,
        "kafka": kafka_client.ping,
        "s3": s3.ping,
    }))
    app.register_blueprint(search_routes.bp)
    app.register_blueprint(index_routes.bp)
    app.register_blueprint(document_routes.bp)
    problem.register_error_handlers(app, logger)
    return app


app = create_app()
