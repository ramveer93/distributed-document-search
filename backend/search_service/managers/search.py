"""Search logic: two cache levels in front of one shard.

Postgres is never touched — title, snippet and metadata all come from the
Elasticsearch stored fields, which keeps one datastore on the hot path.
"""
import hashlib
import json
import time

from common.clients import redis_client
from common.config import settings
from common.constants import key_cache_version, key_query
from common.exceptions import ValidationFailed
from common.observability.metrics import cache_lookups

from ..repositories import search_index

_l1: dict[str, tuple[float, dict]] = {}


def run(tenant: str, q: str, page: int, size: int, facets: list[str],
        fuzzy: bool, highlight: bool) -> tuple[dict, str]:
    if page > settings().max_page:
        # from=10000 makes EVERY shard collect 10,020 hits and discard 9,980.
        # the cap is deliberate, not a limitation.
        raise ValidationFailed(
            f"page must be <= {settings().max_page}; use a cursor beyond that")

    key = _cache_key(tenant, q, page, size, facets, fuzzy, highlight)

    cached = _l1_get(key)
    if cached is not None:
        cache_lookups.labels("l1", "hit").inc()
        return cached, "HIT"
    cache_lookups.labels("l1", "miss").inc()

    raw = redis_client.client().get(key)
    if raw:
        cache_lookups.labels("l2", "hit").inc()
        value = json.loads(raw)
        _l1_put(key, value)
        return value, "HIT"
    cache_lookups.labels("l2", "miss").inc()

    started = time.perf_counter()
    result = search_index.search(tenant, q, page, size, facets, fuzzy, highlight)
    value = _shape(result, int((time.perf_counter() - started) * 1000))

    redis_client.client().setex(key, settings().query_cache_ttl, json.dumps(value))
    _l1_put(key, value)
    return value, "MISS"


def _cache_key(tenant, q, page, size, facets, fuzzy, highlight) -> str:
    """Everything that changes the answer goes in the key, nothing else does.

    Tenant is part of it — a key of sha1(query) alone would serve one
    tenant's results to another. user_id and session_id are deliberately
    absent: they would give every user a private cache and collapse the
    hit rate to zero.
    """
    parts = json.dumps({"q": q, "page": page, "size": size,
                        "facets": sorted(facets), "fuzzy": fuzzy,
                        "highlight": highlight}, sort_keys=True)
    version = redis_client.client().get(key_cache_version(tenant)) or "0"
    return key_query(tenant, int(version), hashlib.sha1(parts.encode()).hexdigest())


def _shape(res: dict, took_ms: int) -> dict:
    hits = []
    for h in res["hits"]["hits"]:
        src = h.get("_source", {})
        snippet = None
        if "highlight" in h and h["highlight"].get("body"):
            snippet = h["highlight"]["body"][0]
        hits.append({
            "id": src.get("doc_id"),
            "score": h.get("_score") or 0.0,
            "title": src.get("title", ""),
            "snippet": snippet,
            "metadata": src.get("metadata", {}),
        })

    facets = {
        name: [{"value": b["key"], "count": b["doc_count"]}
               for b in agg.get("buckets", [])]
        for name, agg in (res.get("aggregations") or {}).items()
    }
    return {"total": res["hits"]["total"], "took_ms": took_ms,
            "hits": hits, "facets": facets}


# L1: per-process, so it cannot be invalidated across pods — hence a short
# TTL that self-corrects, rather than the 60 s Redis carries.
def _l1_get(key: str):
    entry = _l1.get(key)
    if not entry:
        return None
    expires, value = entry
    if expires < time.monotonic():
        _l1.pop(key, None)
        return None
    return value


def _l1_put(key: str, value: dict) -> None:
    if len(_l1) > 10_000:
        _l1.clear()
    _l1[key] = (time.monotonic() + settings().l1_cache_ttl, value)
