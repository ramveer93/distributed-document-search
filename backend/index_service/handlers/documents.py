"""HTTP marshalling only — no business logic."""
import json

from flask import jsonify
from flask import request as flask_request
from pydantic import ValidationError

from common.context import context
from common.exceptions import PayloadTooLarge, ValidationFailed

from ..managers import documents as manager
from ..schemas import DocumentAccepted, DocumentIn


MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def create():
    if (flask_request.content_type or "").startswith("multipart/form-data"):
        return _create_from_upload()
    try:
        payload = DocumentIn.model_validate(flask_request.get_json(silent=True) or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise ValidationFailed(f"{'.'.join(map(str, first['loc']))}: {first['msg']}")

    ctx = context()
    doc_id, version = manager.index_document(
        tenant=ctx.tenant,
        title=payload.title,
        body=payload.body,
        content_type=payload.content_type,
        metadata=payload.metadata,
        request_id=ctx.request_id,
    )

    return _accepted(doc_id, ctx.tenant, version)


def _create_from_upload():
    """multipart/form-data: file + optional title and metadata."""
    upload = flask_request.files.get("file")
    if upload is None or not upload.filename:
        raise ValidationFailed("multipart request needs a 'file' part")

    raw = upload.read()
    if not raw:
        raise ValidationFailed("uploaded file is empty")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise PayloadTooLarge(f"file exceeds {MAX_UPLOAD_BYTES // (1024*1024)} MB")

    try:
        metadata = json.loads(flask_request.form.get("metadata") or "{}")
        if not isinstance(metadata, dict):
            raise ValueError
    except ValueError:
        raise ValidationFailed("metadata must be a JSON object")

    ctx = context()
    doc_id, version = manager.index_upload(
        tenant=ctx.tenant,
        title=flask_request.form.get("title") or upload.filename,
        raw=raw,
        content_type=upload.mimetype or "application/octet-stream",
        filename=upload.filename,
        metadata=metadata,
        request_id=ctx.request_id,
    )
    return _accepted(doc_id, ctx.tenant, version)


def _accepted(doc_id: str, tenant: str, version: int):
    body = DocumentAccepted(id=doc_id, tenant=tenant,
                            status="PENDING", version=version)
    # 202, not 201: the document is durably stored but not yet searchable.
    # Saying 201 would promise something the write path has not delivered.
    response = jsonify(body.model_dump())
    response.status_code = 202
    response.headers["Location"] = f"/documents/{doc_id}"
    return response
