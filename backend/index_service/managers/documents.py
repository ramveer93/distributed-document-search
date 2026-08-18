"""Write-path business logic.

Two rules the diagram turns on:

  1. S3 always lands BEFORE the row. A row can never point at bytes that are
     not there yet. The reverse leaves an orphan blob, which is harmless and
     a lifecycle rule sweeps it.
  2. The 256 KB threshold is a backend implementation detail. No client sees
     it, and it can be retuned without touching a single caller.
"""
import uuid

from common.clients import s3
from common.config import settings
from common.constants import s3_raw_key

from ..repositories import documents as repo


def index_upload(tenant: str, title: str, raw: bytes, content_type: str,
                 filename: str, metadata: dict,
                 request_id: str | None) -> tuple[str, int]:
    """Uploaded files ALWAYS go to S3, whatever their size.

    Not a size decision: a Postgres TEXT column cannot hold PDF bytes at all.
    The indexer sniffs and extracts later — the API never parses anything.
    """
    doc_id = uuid.uuid4()
    key = s3_raw_key(tenant, str(doc_id))
    s3.put(key, raw, content_type)          # bytes land before the row
    return repo.create_with_outbox(
        doc_id, tenant, title, None, key, content_type, len(raw),
        {**metadata, "filename": filename}, request_id)


def index_document(tenant: str, title: str, body: str, content_type: str,
                   metadata: dict, request_id: str | None) -> tuple[str, int]:
    encoded = body.encode("utf-8")
    size = len(encoded)

    # minted once, here: the S3 key is derived from it, so the object is
    # findable by document id. generating a second uuid in the repository
    # would leave the bytes at an address nothing else can compute.
    doc_id = uuid.uuid4()

    if size <= settings().inline_body_max_bytes:
        # small: one atomic INSERT, no S3 hop, no ordering rule to get wrong
        return repo.create_with_outbox(
            doc_id, tenant, title, body, None, content_type, size,
            metadata, request_id)

    # large: S3 lands FIRST, so the row can never point at bytes that are not
    # there. the reverse leaves an orphan blob, which is harmless.
    key = s3_raw_key(tenant, str(doc_id))
    s3.put(key, encoded, content_type)

    return repo.create_with_outbox(
        doc_id, tenant, title, None, key, content_type, size,
        metadata, request_id)
