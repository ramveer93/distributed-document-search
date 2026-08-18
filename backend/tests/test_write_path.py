"""The write path, and the two ordering rules it turns on.

Both rules are invisible in normal operation and only matter when something
crashes mid-request, which is exactly why they need tests rather than
comments.
"""
import uuid

import pytest

from common.constants import s3_raw_key
from index_service.managers import documents as manager


@pytest.fixture
def repo(monkeypatch, fake_s3):
    """Captures the insert, and shares the S3 call log so relative ordering
    between the two is observable."""
    class Repo:
        def __init__(self):
            self.calls = []

        def create_with_outbox(self, doc_id, tenant, title, body, s3_key,
                               content_type, size, metadata, request_id):
            self.calls.append(dict(
                doc_id=doc_id, tenant=tenant, title=title, body=body,
                s3_key=s3_key, content_type=content_type, size=size,
                metadata=metadata, request_id=request_id))
            fake_s3.calls.append(("insert", str(doc_id)))
            return str(doc_id), 1

        @property
        def last(self):
            return self.calls[-1]

    fake = Repo()
    monkeypatch.setattr(manager, "repo", fake)
    return fake


class TestInlineThreshold:
    def test_a_small_body_stays_in_the_row(self, repo, fake_s3, override):
        override(inline_body_max_bytes=256 * 1024)
        manager.index_document("acme", "Policy", "short body", "text/plain", {}, "r-1")

        assert repo.last["body"] == "short body"
        assert repo.last["s3_key"] is None
        assert fake_s3.objects == {}, "no S3 hop for a small document"

    def test_a_large_body_goes_to_s3_and_the_row_holds_only_a_key(
            self, repo, fake_s3, override):
        override(inline_body_max_bytes=1024)
        body = "x" * 5000
        manager.index_document("acme", "Big", body, "text/plain", {}, "r-1")

        assert repo.last["body"] is None
        assert repo.last["s3_key"] is not None
        assert fake_s3.objects[repo.last["s3_key"]] == body.encode()

    def test_the_boundary_is_inclusive(self, repo, fake_s3, override):
        override(inline_body_max_bytes=100)
        manager.index_document("acme", "Edge", "x" * 100, "text/plain", {}, None)
        assert repo.last["body"] is not None, "exactly at the limit stays inline"

        manager.index_document("acme", "Edge", "x" * 101, "text/plain", {}, None)
        assert repo.last["body"] is None

    def test_the_threshold_is_measured_in_bytes_not_characters(
            self, repo, fake_s3, override):
        """A multibyte string is longer than len() suggests, and a TEXT
        column is sized in bytes."""
        override(inline_body_max_bytes=10)
        manager.index_document("acme", "Unicode", "文書" * 4, "text/plain", {}, None)
        assert repo.last["body"] is None, "12 bytes of UTF-8, not 4 characters"

    def test_the_recorded_size_is_the_encoded_length(self, repo, fake_s3, override):
        override(inline_body_max_bytes=256 * 1024)
        manager.index_document("acme", "Unicode", "文書", "text/plain", {}, None)
        assert repo.last["size"] == 6


class TestOrderingRules:
    def test_the_bytes_land_before_the_row(self, repo, fake_s3, override):
        """A row can never point at bytes that are not there. The reverse
        leaves an orphan blob, which a lifecycle rule sweeps and nobody
        notices."""
        override(inline_body_max_bytes=10)
        manager.index_document("acme", "Big", "x" * 500, "text/plain", {}, None)

        kinds = [kind for kind, _ in fake_s3.calls]
        assert kinds.index("put") < kinds.index("insert")

    def test_the_s3_key_is_derived_from_the_id_the_row_gets(
            self, repo, fake_s3, override):
        """Minting a second uuid inside the repository would leave the bytes
        at an address nothing else can compute — findable only by listing the
        bucket."""
        override(inline_body_max_bytes=10)
        manager.index_document("acme", "Big", "x" * 500, "text/plain", {}, None)

        doc_id = str(repo.last["doc_id"])
        assert repo.last["s3_key"] == s3_raw_key("acme", doc_id)
        assert doc_id in repo.last["s3_key"]

    def test_the_key_is_tenant_prefixed(self, repo, fake_s3, override):
        """So a bucket policy can scope access per tenant, and so the console
        is navigable when debugging."""
        override(inline_body_max_bytes=10)
        manager.index_document("globex", "Big", "x" * 500, "text/plain", {}, None)
        assert repo.last["s3_key"].startswith("globex/")

    def test_each_document_gets_a_fresh_id(self, repo, fake_s3, override):
        override(inline_body_max_bytes=256 * 1024)
        for _ in range(3):
            manager.index_document("acme", "t", "b", "text/plain", {}, None)
        ids = {str(c["doc_id"]) for c in repo.calls}
        assert len(ids) == 3
        assert all(uuid.UUID(i) for i in ids)


class TestFileUpload:
    def test_an_uploaded_file_always_goes_to_s3(self, repo, fake_s3, override):
        """Not a size decision — a Postgres TEXT column cannot hold PDF bytes
        at all, so even a tiny upload takes the S3 path."""
        override(inline_body_max_bytes=1024 * 1024)
        manager.index_upload("acme", "Tiny", b"%PDF-1.4 tiny",
                             "application/pdf", "tiny.pdf", {}, None)

        assert repo.last["body"] is None
        assert repo.last["s3_key"] is not None
        assert fake_s3.objects[repo.last["s3_key"]] == b"%PDF-1.4 tiny"

    def test_the_api_never_parses_the_file(self, repo, fake_s3):
        """Extraction belongs in a worker that can be killed and scaled on
        its own. If the API parsed uploads, a malformed PDF would be a
        request-path hang."""
        manager.index_upload("acme", "Broken", b"%PDF-1.4 truncated garbage",
                             "application/pdf", "broken.pdf", {}, None)
        assert repo.calls, "a corrupt file must still be accepted and stored"

    def test_the_filename_is_kept_in_metadata(self, repo, fake_s3):
        """The indexer uses it to disambiguate OOXML containers, which all
        share the same magic bytes."""
        manager.index_upload("acme", "Report", b"PK\x03\x04...",
                             "application/vnd...", "q3.docx", {"dept": "fin"}, None)
        assert repo.last["metadata"] == {"dept": "fin", "filename": "q3.docx"}

    def test_the_recorded_size_is_the_byte_count(self, repo, fake_s3):
        manager.index_upload("acme", "F", b"12345", "application/pdf",
                             "f.pdf", {}, None)
        assert repo.last["size"] == 5


class TestTracePropagation:
    def test_the_request_id_is_stored_for_the_relay_to_carry(self, repo, fake_s3):
        """This is what lets one request id follow a document across the
        Kafka boundary into the indexer's logs."""
        manager.index_document("acme", "t", "b", "text/plain", {}, "r-abc123")
        assert repo.last["request_id"] == "r-abc123"

    def test_a_missing_request_id_is_not_an_error(self, repo, fake_s3):
        manager.index_document("acme", "t", "b", "text/plain", {}, None)
        assert repo.last["request_id"] is None
