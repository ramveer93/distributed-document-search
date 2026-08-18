"""Outbox draining and status write-back."""
from datetime import datetime, timezone

from sqlalchemy import select, update

from common.constants import STATUS_FAILED, STATUS_LIVE
from common.db import Document, IndexOutbox, session


def claim_unpublished(limit: int = 100) -> list[dict]:
    """FOR UPDATE SKIP LOCKED so concurrent relays grab DIFFERENT batches
    instead of blocking on each other."""
    with session() as s:
        rows = s.scalars(
            select(IndexOutbox)
            .where(IndexOutbox.published_at.is_(None))
            .order_by(IndexOutbox.seq)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()

        claimed = [{"seq": r.seq, "doc_id": str(r.doc_id), "tenant": r.tenant,
                    "op": r.op, "version": r.version,
                    "request_id": r.request_id} for r in rows]

        if claimed:
            s.execute(
                update(IndexOutbox)
                .where(IndexOutbox.seq.in_([c["seq"] for c in claimed]))
                .values(published_at=datetime.now(timezone.utc))
            )
    return claimed


def load(tenant: str, doc_id: str) -> dict | None:
    with session() as s:
        d = s.scalar(select(Document).where(Document.tenant == tenant,
                                            Document.doc_id == doc_id))
        if not d:
            return None
        return {"doc_id": str(d.doc_id), "tenant": d.tenant, "title": d.title,
                "body": d.body, "s3_key": d.s3_key, "metadata": d.doc_metadata,
                "version": d.version, "status": d.status,
                "created_at": d.created_at.isoformat()}


def mark(tenant: str, doc_id: str, status: str, reason: str | None = None) -> None:
    with session() as s:
        s.execute(
            update(Document)
            .where(Document.tenant == tenant, Document.doc_id == doc_id)
            .values(status=status, failure_reason=reason)
        )


def mark_live(tenant: str, doc_id: str) -> None:
    mark(tenant, doc_id, STATUS_LIVE)


def mark_failed(tenant: str, doc_id: str, reason: str) -> None:
    """A failure is a visible state, not a silent drop — it shows up on
    GET /documents/{id} where support can see it."""
    mark(tenant, doc_id, STATUS_FAILED, reason[:500])
