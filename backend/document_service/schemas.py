from typing import Any

from typing import Literal

from pydantic import BaseModel


class Links(BaseModel):
    raw: str


class ProgressStep(BaseModel):
    key: str
    label: str
    state: Literal["done", "active", "pending", "failed"]
    detail: str | None = None


class DocumentSummary(BaseModel):
    id: str
    title: str
    status: str
    content_type: str
    byte_size: int
    metadata: dict[str, Any]
    updated_at: str


class DocumentList(BaseModel):
    total: int
    page: int
    size: int
    items: list[DocumentSummary]


class DocumentOut(BaseModel):
    id: str
    tenant: str
    title: str
    status: str
    version: int
    content_type: str
    byte_size: int
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    failure_reason: str | None = None
    # inline only when the body lives in the row. anything larger is a link,
    # so the response size never tracks the document size.
    body: str | None = None
    links: Links | None = None
    # derived from the row plus its outbox entry — no extra columns needed
    progress: list[ProgressStep] = []
