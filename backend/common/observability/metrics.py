"""Metric definitions and the RED middleware.

Self-contained: importing this module registers nothing and starts nothing.
A service opts in by calling install_http_metrics(app, service).
"""
import time

from flask import g, request
from prometheus_client import Counter, Histogram

# --------------------------------------------------------------------- RED
# Buckets are chosen around the actual budget: ~10 ms on a cache hit, ~140 ms
# on a miss, 500 ms SLO. Default buckets would put all of that in two bins.
_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)

http_requests = Counter(
    "http_requests_total", "HTTP requests",
    ["service", "method", "route", "status"])

http_duration = Histogram(
    "http_request_duration_seconds", "HTTP request duration",
    ["service", "method", "route"], buckets=_BUCKETS)

# ------------------------------------------------------------------ caches
cache_lookups = Counter(
    "cache_lookups_total", "Cache lookups by layer and outcome",
    ["layer", "result"])          # layer=l1|l2  result=hit|miss

# NOTE: pipeline metrics (consumer lag, documents processed, relay throughput)
# live in pipeline.py and are imported ONLY by the indexer. Defining them here
# would publish them from every process that imports this module.


def install_http_metrics(app, service: str) -> None:
    """Adds its own before/after hooks — Flask supports several, so this does
    not touch the auth or logging middleware."""

    @app.before_request
    def _metrics_start():
        g._metrics_t0 = time.perf_counter()

    @app.after_request
    def _metrics_end(response):
        # the ROUTE RULE, never request.path: /documents/<doc_id> is one label
        # value, while the raw path would mint a new time series per document
        # and take the whole Prometheus instance down with it
        route = request.url_rule.rule if request.url_rule else "<unmatched>"
        elapsed = time.perf_counter() - getattr(g, "_metrics_t0", time.perf_counter())

        http_requests.labels(service, request.method, route,
                             str(response.status_code)).inc()
        http_duration.labels(service, request.method, route).observe(elapsed)
        return response
