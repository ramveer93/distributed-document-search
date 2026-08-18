"""Indexer-only metrics.

Deliberately NOT exported from the package __init__. Anything defined in a
shared module is created in every process that imports it, so the API and
gateway would publish a kafka_consumer_lag they can never set — and in
gunicorn's multiprocess mode a Gauge is emitted once per worker pid, so one
meaningless value becomes several overlapping series.

Only the indexer imports this, and the indexer is single-process.
"""
from prometheus_client import Counter, Gauge

documents_processed = Counter(
    "documents_processed_total", "Documents applied to the search index",
    ["op"])                        # op=index|delete

documents_dead_lettered = Counter(
    "documents_dead_lettered_total", "Events that exhausted retries", ["op"])

relay_published = Counter(
    "relay_published_total", "Outbox rows published to Kafka")

consumer_lag = Gauge(
    "kafka_consumer_lag",
    "Uncommitted messages on the index topic. Sustained growth means the "
    "search index is drifting from the source of truth.")
