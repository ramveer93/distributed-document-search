"""Fetch, download and delete.

The recurring theme is that every failure mode looks identical from outside:
a document that does not exist, a document belonging to someone else, and a
malformed id all return the same 404.
"""
import pytest

from common.exceptions import NotFound
from document_service.managers import documents as manager
from factories import doc_row

DOC_ID = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def repo(monkeypatch):
    class Repo:
        def __init__(self):
            self.rows = {("acme", DOC_ID): doc_row()}
            self.outbox = {}
            self.deleted = []

        def get(self, tenant, doc_id):
            row = self.rows.get((tenant, doc_id))
            return dict(row) if row else None

        def outbox_state(self, tenant, doc_id):
            return self.outbox.get((tenant, doc_id))

        def soft_delete(self, tenant, doc_id, request_id):
            if (tenant, doc_id) not in self.rows:
                return False
            self.deleted.append((tenant, doc_id, request_id))
            return True

        def list_for_tenant(self, tenant, page, size, status):
            rows = [r for (t, _), r in self.rows.items() if t == tenant
                    and (status is None or r["status"] == status)]
            return len(rows), rows

    fake = Repo()
    monkeypatch.setattr(manager, "repo", fake)
    return fake


# ------------------------------------------------------------- id handling

class TestMalformedIds:
    @pytest.mark.parametrize("bad", [
        "not-a-uuid", "", "12345", "'; DROP TABLE documents;--",
        "../../etc/passwd", "11111111-1111-4111-8111",
    ])
    def test_a_malformed_id_is_a_404_not_a_500(self, repo, bad):
        """Without the guard the string reaches a UUID column, Postgres
        raises, and a plain client mistake surfaces as a server error — plus
        noise in every error dashboard."""
        with pytest.raises(NotFound):
            manager.fetch("acme", bad)

    def test_the_id_is_validated_before_any_query_runs(self, repo, monkeypatch):
        monkeypatch.setattr(repo, "get", _explode)
        with pytest.raises(NotFound):
            manager.fetch("acme", "nonsense")

    def test_delete_and_download_validate_too(self, repo):
        for call in (manager.remove, manager.presigned_download):
            with pytest.raises(NotFound):
                (call("acme", "nonsense", None) if call is manager.remove
                 else call("acme", "nonsense"))


def _explode(*_a, **_k):
    raise AssertionError("the repository should not have been reached")


# --------------------------------------------------------- tenant isolation

class TestTenantIsolation:
    def test_another_tenants_document_is_404_not_403(self, repo):
        """403 would confirm the document exists, which turns id enumeration
        into an information leak. The tenant-scoped query gives us this for
        free — there is no ownership check to forget."""
        with pytest.raises(NotFound):
            manager.fetch("globex", DOC_ID)

    def test_a_missing_document_and_a_forbidden_one_are_indistinguishable(self, repo):
        with pytest.raises(NotFound) as absent:
            manager.fetch("acme", OTHER)
        with pytest.raises(NotFound) as forbidden:
            manager.fetch("globex", DOC_ID)
        assert str(absent.value) == str(forbidden.value)

    def test_deleting_another_tenants_document_is_404(self, repo):
        with pytest.raises(NotFound):
            manager.remove("globex", DOC_ID, "r-1")
        assert repo.deleted == []

    def test_downloading_another_tenants_document_is_404(self, repo, fake_s3):
        repo.rows[("acme", DOC_ID)]["s3_key"] = "acme/x/raw"
        with pytest.raises(NotFound):
            manager.presigned_download("globex", DOC_ID)
        assert fake_s3.calls == [], "nothing should be signed"

    def test_listing_is_scoped_to_the_caller(self, repo):
        repo.rows[("globex", OTHER)] = doc_row(doc_id=OTHER, tenant="globex")
        total, rows = manager.list_documents("acme", 1, 20, None)
        assert total == 1
        assert all(r["tenant"] == "acme" for r in rows)


# ------------------------------------------------------------------- fetch

class TestFetch:
    def test_an_inline_body_comes_back_in_the_response(self, repo):
        row = manager.fetch("acme", DOC_ID)
        assert row["body"] == "customers may request refunds within 30 days"
        assert row["links"] is None

    def test_a_stored_object_becomes_a_link_not_bytes(self, repo):
        """Bytes never travel inside a JSON response — a 200 MB document
        would otherwise be a 200 MB JSON string."""
        repo.rows[("acme", DOC_ID)].update(s3_key="acme/x/raw", body=None)
        row = manager.fetch("acme", DOC_ID)
        assert row["body"] is None
        assert row["links"] == {"raw": f"/documents/{DOC_ID}/raw"}

    def test_progress_is_attached(self, repo):
        assert [s["key"] for s in manager.fetch("acme", DOC_ID)["progress"]] \
            == ["stored", "queued", "indexed"]


# ---------------------------------------------------------------- download

class TestPresignedDownload:
    def test_ownership_is_checked_before_signing(self, repo, fake_s3):
        """Presigning is a local HMAC with no S3 call, so nothing stops us
        signing a URL for a document we do not own — except this check."""
        repo.rows[("acme", DOC_ID)]["s3_key"] = "acme/x/raw"
        url = manager.presigned_download("acme", DOC_ID)
        assert "acme/x/raw" in url
        assert fake_s3.calls == [("presign", "acme/x/raw")]

    def test_a_document_with_no_object_is_404(self, repo, fake_s3):
        with pytest.raises(NotFound):
            manager.presigned_download("acme", DOC_ID)
        assert fake_s3.calls == []


# ------------------------------------------------------------------ delete

class TestDelete:
    def test_a_soft_delete_carries_the_request_id(self, repo):
        manager.remove("acme", DOC_ID, "r-abc")
        assert repo.deleted == [("acme", DOC_ID, "r-abc")]

    def test_deleting_a_missing_document_is_404(self, repo):
        with pytest.raises(NotFound):
            manager.remove("acme", OTHER, "r-1")


# ---------------------------------------------------------------- progress

class TestProgress:
    def state(self, **kwargs):
        steps = manager.build_progress(**kwargs)
        return {s["key"]: s["state"] for s in steps}

    def test_freshly_stored(self):
        """The row committed with its outbox row in one transaction, so
        `stored` is done the moment the document is visible at all."""
        assert self.state(status="PENDING", outbox=None, failure_reason=None) \
            == {"stored": "done", "queued": "active", "indexed": "pending"}

    def test_published_to_kafka(self):
        assert self.state(status="PENDING", outbox={"published_at": "2026-01-01"},
                          failure_reason=None) \
            == {"stored": "done", "queued": "done", "indexed": "active"}

    def test_live(self):
        assert self.state(status="LIVE", outbox={"published_at": "2026-01-01"},
                          failure_reason=None) \
            == {"stored": "done", "queued": "done", "indexed": "done"}

    def test_live_is_complete_even_if_the_outbox_row_is_gone(self):
        """A retention job may have swept the outbox row long before anyone
        opens the document. LIVE is the stronger signal."""
        assert self.state(status="LIVE", outbox=None, failure_reason=None) \
            == {"stored": "done", "queued": "done", "indexed": "done"}

    def test_a_failure_marks_the_stage_it_stopped_at(self):
        """Not a spinner that never resolves — the point of the whole
        three-stage display is that each state is backed by something real."""
        states = self.state(status="FAILED", outbox={"published_at": "x"},
                            failure_reason="no text layer — needs OCR")
        assert states == {"stored": "done", "queued": "done", "indexed": "failed"}

    def test_the_failure_reason_is_shown_on_the_failed_stage(self):
        steps = manager.build_progress("FAILED", {"published_at": "x"},
                                       "no text layer — needs OCR")
        failed = [s for s in steps if s["state"] == "failed"]
        assert len(failed) == 1
        assert failed[0]["detail"] == "no text layer — needs OCR"

    def test_only_one_stage_is_ever_marked_failed(self):
        states = self.state(status="FAILED", outbox=None, failure_reason="relay died")
        assert list(states.values()).count("failed") == 1
        assert states == {"stored": "done", "queued": "failed", "indexed": "pending"}

    def test_nothing_stays_active_once_it_has_failed(self):
        """An active spinner alongside a failure is the exact UX the stepper
        exists to avoid."""
        states = self.state(status="FAILED", outbox=None, failure_reason="x")
        assert "active" not in states.values()
