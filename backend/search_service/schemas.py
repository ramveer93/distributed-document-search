from typing import Any, Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    q: str = Field(min_length=1, max_length=256)
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
    facets: list[str] = Field(default_factory=list)
    fuzzy: bool = True
    highlight: bool = True


class Total(BaseModel):
    # never flatten this to an int: past track_total_hits Elasticsearch
    # reports a lower bound, and rendering "10000" as exact is a lie
    value: int
    relation: Literal["eq", "gte"]


class Hit(BaseModel):
    id: str
    score: float
    title: str
    snippet: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchOut(BaseModel):
    query: str
    tenant: str
    total: Total
    page: int
    size: int
    took_ms: int
    cache: Literal["HIT", "MISS"]
    hits: list[Hit]
    facets: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
