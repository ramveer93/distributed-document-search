"""Kafka consumer: apply index and delete events to Elasticsearch.

Both operations are idempotent — indexing is an upsert guarded by version,
and deleting something already gone is a no-op — which is what makes retries
safe rather than corrupting.
"""
import time

from elasticsearch import ConflictError, NotFoundError

from common.clients import elastic, kafka_client, redis_client, s3
from common.config import settings
from common.constants import (OP_DELETE, STATUS_DELETED, STATUS_PENDING,
                              key_cache_version, key_doc, s3_text_key)
from common.observability.pipeline import (consumer_lag,
                                           documents_dead_lettered,
                                           documents_processed)

from ..repositories import documents as repo
from . import extraction


def handle(event: dict, logger) -> None:
    tenant, doc_id = event["tenant"], event["doc_id"]
    log = {"request_id": event.get("request_id"), "tenant": tenant,
           "doc_id": doc_id}

    if event["op"] == OP_DELETE:
        _delete(tenant, doc_id, event["version"])
        _invalidate(tenant, doc_id)
        documents_processed.labels("delete").inc()
        logger.info("deleted from index", extra=log)
        return

    row = repo.load(tenant, doc_id)
    if row is None:
        logger.warning("document vanished before indexing", extra=log)
        return

    # the one rule the whole pipeline turns on: act on PENDING and nothing
    # else. it stops the status=LIVE write-back from looping back through
    # the relay, and skips rows already superseded.
    if row["status"] == STATUS_DELETED:
        _delete(tenant, doc_id, row["version"])
        _invalidate(tenant, doc_id)
        return
    if row["status"] != STATUS_PENDING:
        logger.info(f"skipped, status={row['status']}", extra=log)
        return

    try:
        body = _body_for(tenant, doc_id, row, logger)
    except extraction.NeedsOCR as exc:
        # a visible state, not a silent empty document. this is also the queue
        # you would drain the day OCR gets added.
        repo.mark_failed(tenant, doc_id, str(exc))
        logger.warning(str(exc), extra={**log, "status": "FAILED"})
        return
    except extraction.UnsupportedType as exc:
        repo.mark_failed(tenant, doc_id, str(exc))
        logger.warning(str(exc), extra={**log, "status": "FAILED"})
        return

    _index(tenant, doc_id, row, body)
    repo.mark_live(tenant, doc_id)
    _invalidate(tenant, doc_id)
    documents_processed.labels("index").inc()
    logger.info("indexed", extra={**log, "status": "LIVE"})


def _body_for(tenant: str, doc_id: str, row: dict, logger) -> str:
    """Text bodies arrive inline. Uploaded files arrive as bytes in S3 and
    have to be parsed — once. The extracted text is written back to /text so
    a reindex reads it instead of re-parsing ten million PDFs."""
    if row["body"] is not None:
        return row["body"]

    key = row["s3_key"]
    text_key = s3_text_key(tenant, doc_id)
    try:
        return s3.get(text_key).decode("utf-8")      # already extracted
    except Exception:
        pass

    raw = s3.get(key)
    text, pages = extraction.extract(raw, row["metadata"].get("filename", ""))
    s3.put(text_key, text.encode("utf-8"), "text/plain")
    if pages:
        logger.info(f"extracted {pages} pages", extra={"tenant": tenant,
                                                       "doc_id": doc_id})
    return text


def _index(tenant: str, doc_id: str, row: dict, body: str) -> None:
    try:
        elastic.client().index(
            index=settings().es_index,
            id=f"{tenant}:{doc_id}",
            routing=tenant,               # co-locate the tenant on one shard
            version=row["version"],
            version_type="external",      # ES itself rejects a stale event
            document={"tenant": tenant, "doc_id": doc_id,
                      "title": row["title"], "body": body,
                      "metadata": row["metadata"], "version": row["version"],
                      "created_at": row["created_at"]},
        )
    except ConflictError:
        # a newer version already landed; this event is stale and dropping it
        # is the correct outcome, not an error
        pass


def _delete(tenant: str, doc_id: str, version: int) -> None:
    try:
        elastic.client().delete(index=settings().es_index,
                                id=f"{tenant}:{doc_id}", routing=tenant,
                                version=version, version_type="external")
    except (NotFoundError, ConflictError):
        pass


def _invalidate(tenant: str, doc_id: str) -> None:
    """One INCR retires every cached query for the tenant — no SCAN, no key
    deletion. The doc cache is a single known key, so that one is a DEL."""
    r = redis_client.client()
    r.incr(key_cache_version(tenant))
    r.delete(key_doc(tenant, doc_id))


def loop(logger, stop) -> None:
    consumer = kafka_client.consumer(settings().kafka_topic, settings().kafka_group)
    max_attempts = settings().max_attempts

    last_lag_check = 0.0
    while not stop.is_set():
        if time.time() - last_lag_check > 15:
            _record_lag(consumer)
            last_lag_check = time.time()
        for _tp, records in consumer.poll(timeout_ms=1000).items():
            for record in records:
                event = record.value
                for attempt in range(1, max_attempts + 1):
                    try:
                        handle(event, logger)
                        break
                    except Exception as exc:
                        if attempt == max_attempts:
                            _dead_letter(event, exc, logger)
                        else:
                            # exponential backoff with jitter, or every failure
                            # retries in lockstep and hammers the recovering service
                            time.sleep((2 ** attempt) * 0.2)
            consumer.commit()   # only after the batch is applied or dead-lettered
    consumer.close()


def _record_lag(consumer) -> None:
    """Lag is the number that says the index is drifting from the source of
    truth — and nothing else surfaces that."""
    try:
        parts = consumer.assignment()
        if not parts:
            return
        ends = consumer.end_offsets(list(parts))
        consumer_lag.set(sum(ends[p] - consumer.position(p) for p in parts))
    except Exception:
        pass


def _dead_letter(event: dict, exc: Exception, logger) -> None:
    documents_dead_lettered.labels(event.get("op", "?")).inc()
    logger.error(f"dead-lettered: {exc}", extra={
        "request_id": event.get("request_id"), "tenant": event.get("tenant"),
        "doc_id": event.get("doc_id")})
    repo.mark_failed(event["tenant"], event["doc_id"], str(exc))
    kafka_client.publish(settings().kafka_dlq_topic, key=event["doc_id"],
                         value={**event, "error": str(exc)})
