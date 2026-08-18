"""Outbox relay: Postgres -> Kafka.

Production replaces this loop with Debezium reading the WAL, at which point
the outbox table disappears. The guarantee does not change: the document row
and its outbox row committed together, so nothing can be lost here.
"""
import time

from common.clients import kafka_client
from common.config import settings
from common.observability.pipeline import relay_published

from ..repositories import documents as repo

POLL_SECONDS = 0.5


def drain_once(logger) -> int:
    batch = repo.claim_unpublished()
    for row in batch:
        kafka_client.publish(
            settings().kafka_topic,
            # key = doc_id, so every event for one document lands in the same
            # partition and an UPSERT can never overtake the DELETE after it.
            # keying on tenant would put a whale's whole corpus in one partition.
            key=row["doc_id"],
            value={"doc_id": row["doc_id"], "tenant": row["tenant"],
                   "op": row["op"], "version": row["version"],
                   "request_id": row["request_id"]},
        )
        relay_published.inc()
        logger.info("relayed", extra={"request_id": row["request_id"],
                                      "tenant": row["tenant"],
                                      "doc_id": row["doc_id"]})
    return len(batch)


def loop(logger, stop) -> None:
    while not stop.is_set():
        try:
            if drain_once(logger) == 0:
                time.sleep(POLL_SECONDS)
        except Exception:
            logger.exception("relay failed", extra={"request_id": "-"})
            time.sleep(2)
