"""Persistence for the write path."""
import uuid

from common.constants import OP_UPSERT, STATUS_PENDING
from common.db import Document, IndexOutbox, session


def create_with_outbox(
    doc_id: uuid.UUID, tenant: str, title: str, body: str | None,
    s3_key: str | None, content_type: str, byte_size: int, metadata: dict,
    request_id: str | None,
) -> tuple[str, int]:
    """The document row and its outbox row commit in ONE transaction.

    That is the whole reliability story of the write path: either both land
    or neither does, so a crash can never leave a document that nothing
    downstream knows about. No dual write, nothing to reconcile.
    """
    with session() as s:
        s.add(Document(
            doc_id=doc_id, tenant=tenant, title=title, body=body,
            s3_key=s3_key, content_type=content_type, byte_size=byte_size,
            doc_metadata=metadata, version=1, status=STATUS_PENDING,
        ))
        s.add(IndexOutbox(
            doc_id=doc_id, tenant=tenant, op=OP_UPSERT, version=1,
            request_id=request_id,
        ))
    return str(doc_id), 1
