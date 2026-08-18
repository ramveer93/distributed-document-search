from flask import Response, jsonify, redirect
from flask import request as flask_request

from common.config import settings
from common.context import context

from ..managers import documents as manager
from ..schemas import (DocumentList, DocumentOut, DocumentSummary,
                       Links, ProgressStep)


MAX_PAGE_SIZE = 100


def index():
    """GET /documents — the tenant's documents, newest first."""
    ctx = context()
    page = max(1, int(flask_request.args.get("page", 1)))
    size = min(MAX_PAGE_SIZE, max(1, int(flask_request.args.get("size", 25))))
    status = flask_request.args.get("status") or None

    total, rows = manager.list_documents(ctx.tenant, page, size, status)
    out = DocumentList(total=total, page=page, size=size,
                       items=[DocumentSummary(**r) for r in rows])
    return jsonify(out.model_dump()), 200


def get(doc_id: str):
    ctx = context()
    row = manager.fetch(ctx.tenant, doc_id)
    out = DocumentOut(
        id=row["id"], tenant=row["tenant"], title=row["title"],
        status=row["status"], version=row["version"],
        content_type=row["content_type"], byte_size=row["byte_size"],
        metadata=row["metadata"], created_at=row["created_at"],
        updated_at=row["updated_at"], failure_reason=row["failure_reason"],
        body=row["body"],
        links=Links(**row["links"]) if row["links"] else None,
        progress=[ProgressStep(**p) for p in row.get("progress", [])],
    )
    return jsonify(out.model_dump(exclude_none=True)), 200


def raw(doc_id: str):
    """The bytes never pass through this service — the caller goes to S3.

    Two shapes for two callers:
      302             API clients and curl follow the redirect
      Accept: json    browsers, which cannot attach a bearer token to a plain
                      <a href> navigation. The page fetches the URL WITH auth,
                      then navigates to it — S3 needs no header of ours.
    """
    ctx = context()
    url = manager.presigned_download(ctx.tenant, doc_id)

    if "application/json" in (flask_request.headers.get("Accept") or ""):
        return jsonify({"url": url, "expires_in": settings().s3_presign_ttl}), 200
    return redirect(url, code=302)


def delete(doc_id: str):
    ctx = context()
    manager.remove(ctx.tenant, doc_id, ctx.request_id)
    return Response(status=204)
