"""The scrape endpoint.

Under gunicorn each worker holds its own registry, so a naive endpoint
reports whichever worker happened to answer — counts come out divided by the
worker count and some series vanish entirely. prometheus_client solves this
with a shared directory the workers write into; we aggregate at scrape time.
"""
import os

from flask import Blueprint, Response
from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry,
                               generate_latest, multiprocess)

bp = Blueprint("metrics", __name__)


@bp.get("/metrics")
def metrics():
    """Unauthenticated, but never proxied by the gateway and the API port is
    not published — scraped on the internal network only, like /health/detail.
    """
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        return Response(generate_latest(registry), mimetype=CONTENT_TYPE_LATEST)
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)
