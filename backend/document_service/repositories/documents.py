"""Reads and the soft delete.

Every query filters on tenant in the WHERE clause, so a wrong-tenant lookup
returns no rows rather than depending on a handler remembering to check.
"""
from sqlalchemy import func, select

from common.constants import OP_DELETE, STATUS_DELETED
from common.db import Document, IndexOutbox, session


def get(tenant: str, doc_id: str) -> dict | None:
    with session() as s:
        d = s.scalar(
            select(Document).where(
                Document.tenant == tenant,
                Document.doc_id == doc_id,
                Document.status != STATUS_DELETED,
            )
        )
        if not d:
            return None
        return {
            "id": str(d.doc_id), "tenant": d.tenant, "title": d.title,
            "status": d.status, "version": d.version,
            "content_type": d.content_type, "byte_size": d.byte_size,
            "metadata": d.doc_metadata, "body": d.body, "s3_key": d.s3_key,
            "failure_reason": d.failure_reason,
            "created_at": d.created_at.isoformat(),
            "updated_at": d.updated_at.isoformat(),
        }


def list_for_tenant(tenant: str, page: int, size: int,
                    status: str | None = None) -> tuple[int, list[dict]]:
    """Newest first. Tenant is in the WHERE clause, as everywhere else."""
    with session() as s:
        where = [Document.tenant == tenant, Document.status != STATUS_DELETED]
        if status:
            where.append(Document.status == status)

        total = s.scalar(select(func.count()).select_from(Document).where(*where)) or 0
        rows = s.scalars(
            select(Document).where(*where)
            .order_by(Document.updated_at.desc())
            .offset((page - 1) * size).limit(size)
        ).all()
        return total, [{
            "id": str(d.doc_id), "title": d.title, "status": d.status,
            "content_type": d.content_type, "byte_size": d.byte_size,
            "metadata": d.doc_metadata, "updated_at": d.updated_at.isoformat(),
        } for d in rows]


def outbox_state(tenant: str, doc_id: str) -> dict | None:
    """The most recent outbox entry for a document.

    published_at tells us whether the relay has handed it to Kafka yet, which
    is the one intermediate stage that is real rather than inferred.
    """
    with session() as s:
        row = s.scalars(
            select(IndexOutbox)
            .where(IndexOutbox.tenant == tenant, IndexOutbox.doc_id == doc_id)
            .order_by(IndexOutbox.seq.desc()).limit(1)
        ).first()
        if not row:
            return None
        return {"op": row.op, "published_at": row.published_at,
                "created_at": row.created_at}


def soft_delete(tenant: str, doc_id: str, request_id: str | None) -> bool:
    """Soft, because the row is what tells the async cleanup what to clean.

    The version bump keeps the delete ordered against any in-flight upsert
    for the same document — the indexer drops anything older than what it
    has already applied.
    """
    with session() as s:
        d = s.scalar(
            select(Document).where(
                Document.tenant == tenant,
                Document.doc_id == doc_id,
                Document.status != STATUS_DELETED,
            ).with_for_update()
        )
        if not d:
            return False

        d.status = STATUS_DELETED
        d.version += 1
        s.add(IndexOutbox(doc_id=d.doc_id, tenant=tenant, op=OP_DELETE,
                          version=d.version, request_id=request_id))
    return True
