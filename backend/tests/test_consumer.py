"""The indexer's event handler.

Everything here is downstream of one rule — act on PENDING and nothing else —
which is what stops the LIVE write-back looping back through the relay, and
what makes at-least-once delivery safe.
"""
import pytest
from elasticsearch import ConflictError, NotFoundError

from common.constants import (OP_DELETE, OP_UPSERT, STATUS_DELETED,
                              STATUS_FAILED, STATUS_LIVE, STATUS_PENDING,
                              key_cache_version, key_doc, s3_text_key)
from factories import doc_row, pdf_bytes
from indexer.managers import consumer

DOC_ID = "11111111-1111-4111-8111-111111111111"


@pytest.fixture
def repo(monkeypatch):
    class Repo:
        def __init__(self):
            self.row = doc_row()
            self.live, self.failed = [], []

        def load(self, tenant, doc_id):
            return self.row

        def mark_live(self, tenant, doc_id):
            self.live.append((tenant, doc_id))

        def mark_failed(self, tenant, doc_id, reason):
            self.failed.append((tenant, doc_id, reason))

    fake = Repo()
    monkeypatch.setattr(consumer, "repo", fake)
    return fake


def event(op=OP_UPSERT, **over):
    return {"op": op, "tenant": "acme", "doc_id": DOC_ID, "version": 1,
            "request_id": "r-1", **over}


def conflict():
    return ConflictError("version conflict", {}, {})


def not_found():
    return NotFoundError("missing", {}, {})


# ------------------------------------------------------- the status gate

class TestStatusGate:
    def test_pending_is_indexed(self, repo, es, redis, fake_s3, logger):
        consumer.handle(event(), logger)
        assert len(es.indexed) == 1
        assert repo.live == [("acme", DOC_ID)]

    def test_live_is_skipped(self, repo, es, redis, fake_s3, logger):
        """The write-back that sets LIVE produces another change event. If the
        indexer acted on it, the pipeline would loop forever."""
        repo.row = doc_row(status=STATUS_LIVE)
        consumer.handle(event(), logger)
        assert es.indexed == []
        assert repo.live == []

    def test_failed_is_skipped(self, repo, es, redis, fake_s3, logger):
        repo.row = doc_row(status=STATUS_FAILED)
        consumer.handle(event(), logger)
        assert es.indexed == []

    def test_a_row_marked_deleted_becomes_an_es_delete(self, repo, es, redis, logger):
        """A soft delete is an UPDATE, so it arrives on the same topic with
        the same shape — which is exactly why deletes do not get their own
        topic and lose ordering against the upserts."""
        repo.row = doc_row(status=STATUS_DELETED, version=4)
        consumer.handle(event(), logger)
        assert es.indexed == []
        assert es.deleted[0]["id"] == f"acme:{DOC_ID}"

    def test_a_vanished_row_is_a_warning_not_a_crash(self, repo, es, redis, logger):
        """Hard-deleted between the event being published and consumed. The
        alternative is a dead-lettered message for a document that no longer
        exists."""
        repo.row = None
        consumer.handle(event(), logger)
        assert es.indexed == []
        assert "vanished" in " ".join(logger.messages("warning"))


# ---------------------------------------------------------------- indexing

class TestIndexing:
    def test_the_id_is_tenant_scoped(self, repo, es, redis, fake_s3, logger):
        """Two tenants can hold documents with the same uuid without
        colliding in a shared index."""
        consumer.handle(event(), logger)
        assert es.indexed[0]["id"] == f"acme:{DOC_ID}"

    def test_routing_co_locates_the_tenant(self, repo, es, redis, fake_s3, logger):
        consumer.handle(event(), logger)
        assert es.indexed[0]["routing"] == "acme"

    def test_the_write_routing_matches_the_read_routing(self, repo, es, redis,
                                                        fake_s3, logger):
        """If these ever disagree, documents are written to one shard and
        searched for on another — and every search silently returns nothing."""
        from search_service.repositories import search_index
        consumer.handle(event(), logger)
        search_index.search("acme", "q", 1, 10, [], False, False)
        assert es.indexed[0]["routing"] == es.last_search["routing"]

    def test_the_version_guard_is_external(self, repo, es, redis, fake_s3, logger):
        """Elasticsearch itself rejects an out-of-order event, so a
        redelivered v1 cannot overwrite a v2 that already landed."""
        repo.row = doc_row(version=7)
        consumer.handle(event(), logger)
        assert es.indexed[0]["version"] == 7
        assert es.indexed[0]["version_type"] == "external"

    def test_a_stale_event_is_dropped_rather_than_retried(self, repo, es, redis,
                                                          fake_s3, logger):
        """A version conflict means a newer version already won. Retrying
        would burn the ladder to reach the same correct outcome."""
        es.index_raises = conflict()
        consumer.handle(event(), logger)
        assert repo.live == [("acme", DOC_ID)], "still settles the row"

    def test_the_indexed_document_carries_the_searchable_fields(
            self, repo, es, redis, fake_s3, logger):
        repo.row = doc_row(title="Refund Policy", body="thirty days",
                           metadata={"dept": "finance"})
        consumer.handle(event(), logger)
        doc = es.indexed[0]["document"]
        assert doc["tenant"] == "acme"
        assert doc["title"] == "Refund Policy"
        assert doc["body"] == "thirty days"
        assert doc["metadata"] == {"dept": "finance"}

    def test_the_tenant_is_a_field_on_every_document(self, repo, es, redis,
                                                     fake_s3, logger):
        """The search-side term filter has nothing to filter on otherwise."""
        consumer.handle(event(), logger)
        assert es.indexed[0]["document"]["tenant"] == "acme"


# ---------------------------------------------------------------- deleting

class TestDeleting:
    def test_a_delete_event_removes_it_from_the_index(self, repo, es, redis, logger):
        consumer.handle(event(op=OP_DELETE, version=3), logger)
        assert es.deleted[0]["id"] == f"acme:{DOC_ID}"
        assert es.deleted[0]["routing"] == "acme"
        assert es.deleted[0]["version"] == 3

    def test_a_delete_does_not_read_the_row(self, repo, es, redis, logger):
        """Deletes must work for a row that is already gone."""
        repo.row = None
        consumer.handle(event(op=OP_DELETE), logger)
        assert len(es.deleted) == 1

    def test_deleting_something_already_gone_is_a_no_op(self, repo, es, redis, logger):
        """The property that makes retries safe rather than corrupting."""
        es.delete_raises = not_found()
        consumer.handle(event(op=OP_DELETE), logger)   # must not raise

    def test_a_stale_delete_is_swallowed(self, repo, es, redis, logger):
        es.delete_raises = conflict()
        consumer.handle(event(op=OP_DELETE), logger)


# ------------------------------------------------------- body resolution

class TestBodyResolution:
    def test_an_inline_body_needs_no_s3_call(self, repo, es, redis, fake_s3, logger):
        repo.row = doc_row(body="inline text", s3_key=None)
        consumer.handle(event(), logger)
        assert fake_s3.calls == []
        assert es.indexed[0]["document"]["body"] == "inline text"

    def test_cached_extracted_text_is_reused(self, repo, es, redis, fake_s3, logger):
        """The reason a reindex does not re-parse ten million PDFs."""
        repo.row = doc_row(body=None, s3_key="acme/x/raw")
        fake_s3.objects[s3_text_key("acme", DOC_ID)] = b"previously extracted"

        consumer.handle(event(), logger)

        assert es.indexed[0]["document"]["body"] == "previously extracted"
        assert ("get", "acme/x/raw") not in fake_s3.calls, "should not re-read raw"

    def test_a_first_pass_extracts_and_writes_text_back(self, repo, es, redis,
                                                        fake_s3, logger):
        repo.row = doc_row(body=None, s3_key="acme/x/raw",
                           metadata={"filename": "doc.pdf"})
        fake_s3.objects["acme/x/raw"] = pdf_bytes()

        consumer.handle(event(), logger)

        assert "Refund policy" in es.indexed[0]["document"]["body"]
        assert s3_text_key("acme", DOC_ID) in fake_s3.objects, \
            "extracted text must be cached for the next reindex"

    def test_raw_is_never_overwritten(self, repo, es, redis, fake_s3, logger):
        """/raw is immutable; /text is derived."""
        repo.row = doc_row(body=None, s3_key="acme/x/raw")
        original = pdf_bytes()
        fake_s3.objects["acme/x/raw"] = original
        consumer.handle(event(), logger)
        assert fake_s3.objects["acme/x/raw"] == original


# ------------------------------------------------- permanent failure paths

class TestPermanentFailures:
    def test_a_scan_is_marked_failed_rather_than_indexed_empty(
            self, repo, es, redis, fake_s3, logger):
        """Visible state, not a silent empty document — and this is the queue
        you would drain the day OCR is added."""
        repo.row = doc_row(body=None, s3_key="acme/x/raw")
        fake_s3.objects["acme/x/raw"] = pdf_bytes(text=None)

        consumer.handle(event(), logger)

        assert es.indexed == []
        assert repo.live == []
        assert len(repo.failed) == 1
        assert "OCR" in repo.failed[0][2]

    def test_an_unreadable_file_is_marked_failed(self, repo, es, redis,
                                                 fake_s3, logger):
        repo.row = doc_row(body=None, s3_key="acme/x/raw")
        fake_s3.objects["acme/x/raw"] = b"\x00\xff\xfe" * 100

        consumer.handle(event(), logger)

        assert es.indexed == []
        assert len(repo.failed) == 1

    def test_a_permanent_failure_does_not_propagate_to_the_retry_ladder(
            self, repo, es, redis, fake_s3, logger):
        """handle() returning normally is what keeps it off the ladder.
        Raising would cost three backoffs to reach the same answer."""
        repo.row = doc_row(body=None, s3_key="acme/x/raw")
        fake_s3.objects["acme/x/raw"] = pdf_bytes(text=None)
        consumer.handle(event(), logger)      # must not raise

    def test_a_transient_failure_does_propagate(self, repo, es, redis,
                                                fake_s3, logger):
        """The opposite case: an ES outage must reach the caller so the
        retry ladder and the DLQ can do their job."""
        es.index_raises = RuntimeError("connection refused")
        with pytest.raises(RuntimeError):
            consumer.handle(event(), logger)


# ------------------------------------------------------------ invalidation

class TestInvalidation:
    def test_indexing_bumps_the_tenant_cache_version(self, repo, es, redis,
                                                     fake_s3, logger):
        assert redis.get(key_cache_version("acme")) is None
        consumer.handle(event(), logger)
        assert redis.get(key_cache_version("acme")) == "1"

    def test_deleting_bumps_it_too(self, repo, es, redis, logger):
        """A delete that leaves stale results cached is a document that stays
        findable after it was removed."""
        consumer.handle(event(op=OP_DELETE), logger)
        assert redis.get(key_cache_version("acme")) == "1"

    def test_the_document_cache_entry_is_deleted_outright(self, repo, es, redis,
                                                          fake_s3, logger):
        """One known key, so a DEL is cheaper and more precise than a version
        bump."""
        redis.set(key_doc("acme", DOC_ID), "stale")
        consumer.handle(event(), logger)
        assert redis.get(key_doc("acme", DOC_ID)) is None

    def test_only_the_affected_tenant_is_invalidated(self, repo, es, redis,
                                                     fake_s3, logger):
        redis.set(key_cache_version("globex"), "5")
        consumer.handle(event(), logger)
        assert redis.get(key_cache_version("globex")) == "5"

    def test_a_skipped_document_does_not_invalidate(self, repo, es, redis,
                                                    fake_s3, logger):
        """Otherwise the LIVE write-back would void the cache a second time,
        immediately after the index that produced it."""
        repo.row = doc_row(status=STATUS_LIVE)
        consumer.handle(event(), logger)
        assert redis.get(key_cache_version("acme")) is None
