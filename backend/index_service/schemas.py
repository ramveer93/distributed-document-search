from typing import Any

from pydantic import BaseModel, Field


class DocumentIn(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    body: str = Field(min_length=1)
    content_type: str = Field(default="text/plain", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentAccepted(BaseModel):
    id: str
    tenant: str
    status: str
    version: int
