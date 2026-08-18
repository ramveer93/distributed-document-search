"""Elasticsearch client plus the index template.

routing = tenant means one tenant's documents share a shard, so a search
touches one shard instead of all of them. That is what makes the latency
budget work.
"""
from elasticsearch import Elasticsearch

from ..config import settings

_client: Elasticsearch | None = None

MAPPING = {
    "settings": {
        "number_of_shards": settings().es_shards,
        "number_of_replicas": settings().es_replicas,
        "analysis": {"analyzer": {"default": {"type": "english"}}},
    },
    "mappings": {
        "properties": {
            "tenant":     {"type": "keyword"},
            "doc_id":     {"type": "keyword"},
            "title":      {"type": "text", "fields": {"kw": {"type": "keyword"}}},
            "body":       {"type": "text"},
            "metadata":   {"type": "flattened"},
            "version":    {"type": "long"},
            "created_at": {"type": "date"},
        }
    },
}


def client() -> Elasticsearch:
    global _client
    if _client is None:
        _client = Elasticsearch(settings().es_url, request_timeout=10,
                                retry_on_timeout=True, max_retries=2)
    return _client


def ensure_index() -> None:
    es, name = client(), settings().es_index
    if not es.indices.exists(index=name):
        es.indices.create(index=name, **MAPPING)


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:
        return False
