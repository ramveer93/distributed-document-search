"""Read and delete logic."""
import uuid

from common.clients import s3
from common.constants import (STATUS_DELETED, STATUS_FAILED, STATUS_LIVE,
                              STATUS_PENDING)
from common.exceptions import NotFound

from ..repositories import documents as repo


def build_progress(status: str, outbox: dict | None,
                   failure_reason: str | None) -> list[dict]:
    """Three stages, each backed by something real:

      stored    the row committed (with its outbox row, in one transaction)
      queued    outbox.published_at is set — the relay handed it to Kafka
      indexed   status is LIVE — Elasticsearch has it

    Nothing here is a timer or a guess. A failure marks the stage it stopped
    at rather than showing a spinner that never resolves.
    """
    queued = bool(outbox and outbox.get("published_at"))
    live = status == STATUS_LIVE
    failed = status == STATUS_FAILED

    steps = [
        {"key": "stored", "label": "Stored", "state": "done",
         "detail": "committed with its outbox row, in one transaction"},
        {"key": "queued", "label": "Queued for indexing",
         "state": "done" if (queued or live) else "active",
         "detail": "relay published it to Kafka"},
        {"key": "indexed", "label": "Searchable",
         "state": "done" if live else ("pending" if not queued else "active"),
         "detail": "indexed into Elasticsearch"},
    ]

    if failed:
        # stop at the first stage that had not completed
        for step in steps:
            if step["state"] != "done":
                step["state"] = "failed"
                step["detail"] = failure_reason or "failed"
                break
        for step in steps:
            if step["state"] in ("active", "pending"):
                step["state"] = "pending"
    return steps


def list_documents(tenant: str, page: int, size: int,
                   status: str | None) -> tuple[int, list[dict]]:
    return repo.list_for_tenant(tenant, page, size, status)


def _valid_id(doc_id: str) -> None:
    """A malformed id is a 404, not a 500.

    Without this the string reaches the UUID column and Postgres raises, which
    surfaces as an unhandled 500 — a server error for what is plainly a client
    mistake, and noise in every error dashboard.
    """
    try:
        uuid.UUID(doc_id)
    except (ValueError, AttributeError, TypeError):
        raise NotFound("no such document")


def fetch(tenant: str, doc_id: str) -> dict:
    _valid_id(doc_id)
    """404 rather than 403 when the document belongs to another tenant —
    a 403 would confirm it exists, turning id enumeration into a leak.
    The tenant-scoped query gives us that for free."""
    row = repo.get(tenant, doc_id)
    if not row:
        raise NotFound("no such document")

    # bytes never travel inside a JSON response. a large body becomes a link;
    # a small one is already in the row and comes back inline.
    if row["s3_key"]:
        row["body"] = None
        row["links"] = {"raw": f"/documents/{doc_id}/raw"}
    else:
        row["links"] = None

    row["progress"] = build_progress(
        row["status"], repo.outbox_state(tenant, doc_id), row["failure_reason"])
    return row


def presigned_download(tenant: str, doc_id: str) -> str:
    _valid_id(doc_id)
    """Ownership is checked against Postgres first, then we sign.

    Presigning is a local HMAC — no S3 call — so the service hands out the
    URL without ever touching the bytes. Works the same at 20 KB or 200 MB.
    """
    row = repo.get(tenant, doc_id)
    if not row:
        raise NotFound("no such document")
    if not row["s3_key"]:
        raise NotFound("this document has no stored object")
    return s3.presigned_url(row["s3_key"])


def remove(tenant: str, doc_id: str, request_id: str | None) -> None:
    _valid_id(doc_id)
    if not repo.soft_delete(tenant, doc_id, request_id):
        raise NotFound("no such document")
