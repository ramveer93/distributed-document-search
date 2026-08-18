"""Indexer worker — relay and consumer in one process.

Separate from the API because it is a queue consumer: it scales on lag, not
on request rate, and its work is CPU-bound rather than latency-sensitive.
"""
import signal
import threading

from prometheus_client import start_http_server

from common.clients import elastic, s3
from common.config import settings
from common.db import bootstrap
from common.logging_setup import configure
from common.observability import collectors

from .managers import consumer, relay

SERVICE = "indexer"


def main() -> None:
    logger = configure(SERVICE, settings().log_level)
    bootstrap.create_all()
    elastic.ensure_index()
    s3.ensure_bucket()

    # the worker has no Flask app, so serve /metrics directly
    collectors.register()
    start_http_server(9100)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    threads = [
        threading.Thread(target=relay.loop, args=(logger, stop),
                         name="relay", daemon=True),
        threading.Thread(target=consumer.loop, args=(logger, stop),
                         name="consumer", daemon=True),
    ]
    for t in threads:
        t.start()
    logger.info("indexer ready", extra={"request_id": "-"})

    stop.wait()
    logger.info("shutting down", extra={"request_id": "-"})
    for t in threads:
        t.join(timeout=10)


if __name__ == "__main__":
    main()
