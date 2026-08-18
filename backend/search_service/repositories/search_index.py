"""Elasticsearch access.

The tenant term filter is injected HERE, not by callers. That is the point:
no handler can construct an unscoped query, because building the query is
not something handlers do.
"""
from common.clients import elastic
from common.config import settings


def search(tenant: str, q: str, page: int, size: int, facets: list[str],
           fuzzy: bool, highlight: bool) -> dict:
    body: dict = {
        "query": {
            "bool": {
                # filter, not must: no scoring cost, and Elasticsearch caches
                # it as a bitset, so it is free after the first query
                "filter": [{"term": {"tenant": tenant}}],
                "must": [{
                    "multi_match": {
                        "query": q,
                        "fields": ["title^3", "body"],   # a title hit counts triple
                        **({"fuzziness": "AUTO"} if fuzzy else {}),
                    }
                }],
            }
        },
        "from": (page - 1) * size,
        "size": size,
        "track_total_hits": 10_000,
    }
    if highlight:
        body["highlight"] = {"fields": {"body": {"fragment_size": 140,
                                                 "number_of_fragments": 1}}}
    if facets:
        body["aggs"] = {f: {"terms": {"field": f"metadata.{f}", "size": 10}}
                        for f in facets}

    # routing means one shard answers instead of all of them — the single
    # biggest reason the latency budget works
    return elastic.client().search(
        index=settings().es_index, body=body, routing=tenant)
