# Search Flow — `GET /search`

Diagram: [search-flow.excalidraw](search-flow.excalidraw)

Two caches in front of one shard. **Postgres is never touched.**

---

## Steps

1. `GET /search?q=refund&page=1&size=20`
2. Gateway verifies the JWT, injects `X-Tenant-Id` / `X-User-Id` / `X-Session-Id` / `X-Request-Id`, and **strips** any inbound `X-Tenant-Id`
3. `INCR rl:{tenant}:{minute}` → over limit? `429 + Retry-After`
4. Build the cache key
5. **L1** hit? → return (~1 ms)
6. **L2** hit? → return (~10 ms)
7. Miss → Elasticsearch, `routing=tenant_id`, one shard
8. `SETEX` 60 s, return `200` (~140 ms)

---

## The two caches

| | L1 | L2 |
| --- | --- | --- |
| Where | inside the pod's memory | Redis, over the network |
| Speed | ~0.1 ms | ~2 ms |
| Shared between pods | no | yes |
| Size | ~10k entries | as big as Redis |
| TTL | 5 s | 60 s |
| Invalidatable | no — just expires | yes, via version counter |

**L1** absorbs bursts — 50 people search the same thing within 5 s, one goes to L2, 49 are served from memory.
**L2** shares across the fleet — pod A's result is pod T's hit.

L1's TTL is short because there is no cheap way to tell 20 pods to drop a key. 5 s of staleness, self-correcting.

---

## Cache key

```
q:{tenant}:{v}:{sha1(q|filters|page|size|sort)}
│     │     │
│     │     └─ version, from inv:{tenant}
│     └─────── tenant — omit this and you leak across tenants
└───────────── namespace
```

Example: `q:acme:7:a1b2c3d4...`

**Everything that changes the answer goes in the key. Nothing else does.**

| In | Out |
| --- | --- |
| tenant, query, filters, page, size, sort | user_id, session_id, request_id |

Too little → wrong answers (drop `page`, and page 2 serves page 1).
Too much → zero hits (add `request_id`, and every key is unique).

`user_id` belongs in the key **only** if users within a tenant see different documents — and then key on their permission group, not the user.

---

## Invalidation is one integer

```
indexer finishes a document  →  INCR inv:acme     7 → 8
```

Every new key becomes `q:acme:8:...`. All `:7:` keys are unreachable and expire on their own.

No `SCAN`, no key deletion, no fan-out — one command voids a tenant's entire cached search.

---

## Redis keyspace

```
rl:{tenant}:{yyyymmddhhmm}   INCR + EXPIRE 60    rate limit
inv:{tenant}                 INCR                cache version
q:{tenant}:{v}:{sha1}        JSON, TTL 60 s      query results
```

---

## The Elasticsearch query

```json
POST /docs/_search?routing=acme
{
  "query": { "bool": {
    "filter": [ { "term": { "tenant_id": "acme" } } ],
    "must":   [ { "multi_match": {
        "query": "refund", "fields": ["title^3","text"], "fuzziness": "AUTO" } } ]
  }},
  "highlight": { "fields": { "text": {} } },
  "from": 0, "size": 20
}
```

| | |
| --- | --- |
| `routing=acme` | one shard answers, not all of them — the reason the budget works |
| `filter` not `must` | no scoring cost, cached as a bitset, injected by the repo layer |
| `title^3` | a title match counts three times a body match |

---

## Latency budget

```
gateway + TLS         5 ms
rate limit            1 ms
L2 Redis              2 ms
Elasticsearch    60-120 ms   ← the only expensive hop
serialize             5 ms
                 ---------
cache MISS         ~140 ms      budget: 500 ms
cache HIT           ~10 ms
```

At 1000 QPS with a 60–70% hit rate, only ~350 QPS reaches Elasticsearch.

---

## Tenant isolation — three layers

1. **Gateway** — `X-Tenant-Id` minted from the JWT, inbound stripped
2. **Query** — `term` filter on `tenant_id`, injected by the repository layer
3. **Cache** — every key is tenant-prefixed

Layer 3 is the one people miss. A key of `sha1(query)` alone serves one tenant's results to another.

---

## Pagination

```
page ≤ 500    from / size
page > 500    403 + next_cursor   (search_after)
```

`from=10000` makes every shard collect 10,020 hits and discard 9,980. The cap is deliberate.

---

## What search never touches

- **Postgres** — title, snippet, metadata all come from Elasticsearch stored fields
- **S3** — search returns a snippet, never the file. Raw bytes need `GET /documents/{id}/raw` → 302 presigned, 60 s
