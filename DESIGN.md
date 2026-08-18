# Distributed Document Search — Design

A prototype multi-tenant document search service: 10M+ documents, full-text
search with relevance ranking, p95 under 500 ms, 1000+ searches/sec, tenant
isolation, horizontal scale.

Working code and a Postman collection are in this repository — see the
[README](README.md).

[![Indexing walkthrough](https://img.shields.io/badge/%E2%96%B6%20Indexing%20walkthrough-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/5a0ed4dddc584da3822e1221f36f6a6a) [![Search walkthrough](https://img.shields.io/badge/%E2%96%B6%20Search%20walkthrough-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/eabddd520637489ea87d590c932dc4b2) [![Application demo](https://img.shields.io/badge/%E2%96%B6%20Application%20demo-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/b500cedbf4aa4f3f84798247edb2cd8e)

| | |
| --- | --- |
| [1 · Architecture design](#1--architecture-design) | components, data flow, storage, API, consistency, caching, queue |
| [2 · Production readiness](#2--production-readiness) | scale, resilience, security, observability, performance, operations, SLA, cost |
| [3 · Experience showcase](#3--experience-showcase) | |

---

# 1 · Architecture design

## Where the design comes from

Three requirements settle most of it:

- **p95 under 500 ms at 1000 QPS.** The search path gets one datastore. Joining
  across two is already over budget.
- **10M+ documents, multi-tenant.** Tenant is a query-time filter, not a
  topology decision. One index with routing, not an index per tenant.
- **Documents, not strings.** Real files arrive — PDF, DOCX, HTML. Extraction is
  slow and fails in interesting ways, so it cannot sit on the write path.

The last one forces the shape of everything else: writes are asynchronous, reads
are synchronous. Upload returns `202` once the bytes are durable, and indexing
happens behind a queue.

![Overall architecture](resources/overall.png)

## Components

| | Role | Why |
| --- | --- | --- |
| Gateway | auth, request id, rate limit | one place mints identity; nothing downstream trusts a client header |
| API | document, index, search blueprints | one deployable, three packages — splits later without a rewrite |
| Postgres | source of truth | transactions and a real consistency story |
| Elasticsearch | search index | BM25, analyzers, highlighting, faceting |
| S3 (MinIO) | document bytes | blobs do not belong in a row |
| Kafka | async boundary | "indexer is down" becomes lag, not loss |
| Redis | L2 cache, rate limits | shared ephemeral state |

Postgres full-text search would work at 10M documents. It loses on per-field
relevance tuning (`title^3`), analyzer control, and read scale — it grows by
making one box bigger, and the brief asks for horizontal growth.

Postgres stays the source of truth and Elasticsearch is derived. That one
decision is why the index needs no backups, why a mapping change is safe, and
why search survives a Postgres outage.

## Indexing

![Indexing flow](resources/index.png)

[![Indexing walkthrough](https://img.shields.io/badge/%E2%96%B6%20Indexing%20walkthrough-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/5a0ed4dddc584da3822e1221f36f6a6a)

```
POST /documents  →  bytes durable  →  row + outbox row (one txn)  →  202
                                             │
                   Kafka  ◀── relay ─────────┘
                     │
                  Indexer  →  extract  →  S3 /text  →  Elasticsearch
                                                          │
                                        INCR inv:{tenant} ┘  → status LIVE
```

Three rules:

1. **S3 lands before the row.** A row can never point at bytes that are not
   there. The reverse leaves an orphan blob, which is harmless.
2. **Row and outbox row commit together.** No dual write, so there is no window
   where Postgres has a document and Kafka never hears about it. Production uses
   Debezium on the WAL; the prototype polls the outbox with
   `FOR UPDATE SKIP LOCKED`.
3. **`/raw` is immutable, `/text` is derived.** A reindex reads the extracted
   text instead of re-parsing 10M PDFs.

Size decides the path, and the server decides, never the client: at or under
256 KB the body sits in the row, above that it goes to S3, and above 5 MB the
client would PUT it to S3 directly against a presigned URL (designed, not built
— the prototype streams through the API).

A scanned PDF with no text layer becomes `FAILED: needs OCR` rather than an
empty document that silently matches nothing. Full detail in
[index-flow.md](resources/index-flow.md).

## Search

![Search flow](resources/search.png)

[![Search walkthrough](https://img.shields.io/badge/%E2%96%B6%20Search%20walkthrough-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/eabddd520637489ea87d590c932dc4b2)

```
GET /search  →  L1 (in-process, 5s)  →  L2 (Redis, 60s)  →  Elasticsearch
                    ~0.1 ms                  ~2 ms            60–120 ms
```

Search touches neither Postgres nor S3. Titles, snippets and metadata all come
from Elasticsearch stored fields, which is what keeps the hot path on one
datastore — and, incidentally, why search stays up when Postgres does not.

```json
POST /docs/_search?routing=acme
{
  "query": { "bool": {
    "filter": [ { "term": { "tenant_id": "acme" } } ],
    "must":   [ { "multi_match": { "query": "refund",
                                   "fields": ["title^3", "text"],
                                   "fuzziness": "AUTO" } } ]
  }},
  "highlight": { "fields": { "text": {} } }
}
```

`routing` means one shard answers instead of 24 — the main reason the budget
holds. The tenant term is a `filter` rather than a `must` so it is unscored and
cached as a bitset. `title^3` makes a title match count triple.

Fuzzy matching, highlighting and faceting come free with the engine choice.
Full detail in [search-flow.md](resources/search-flow.md).

## Multi-tenancy

Four layers, so no single mistake is enough:

```
gateway   tenant comes from the JWT claim; an inbound header is never read
service   each verifies the token again — reaching a service directly proves nothing
query     term filter injected by the repository; handlers cannot build an unscoped query
cache     every Redis key is tenant-prefixed
```

The cache layer is the one people forget. A key of `sha1(query)` alone serves
one tenant's results to another.

Cross-tenant reads return **404, not 403**. A 403 confirms the document exists,
which turns id enumeration into a leak.

Elasticsearch routing co-locates a tenant on one shard, but routing is an
optimisation, not isolation — the `term` filter is what enforces it.

## API

Tenancy is never a URL parameter; it is a JWT claim the gateway converts into a
header, stripping any the client sent.

| | |
| --- | --- |
| `POST /auth/login` | → `{ access_token, expires_in }` |
| `POST /documents` | multipart or JSON → `202 { doc_id, status }` |
| `GET /documents` | paginated list |
| `GET /documents/{id}` | metadata, or bytes via `Accept` |
| `GET /documents/{id}/raw` | `302` to a presigned S3 URL, 60 s |
| `DELETE /documents/{id}` | soft delete, propagates to the index |
| `GET /search?q=&page=&size=` | the hot path |
| `GET /health` `/health/detail` `/metrics` | operational |

```jsonc
// GET /search?q=refund
{ "total": 42, "took_ms": 9, "cache": "hit",
  "results": [
    { "doc_id": "…", "title": "Refund Policy", "score": 8.41,
      "highlight": "…may request <em>refunds</em> within 30 days…" } ] }
```

Errors are RFC 7807 and carry the `X-Request-Id` that also appears in the logs
on both sides of Kafka. Contracts and 29 assertions are in the
[Postman collection](postman/deeprunner.postman_collection.json).

## Consistency

Postgres is strongly consistent; search is eventually consistent. A document is
durable and fetchable by id the moment `POST /documents` returns, and searchable
about a second later.

That trade is deliberate. Read-your-writes on search would mean synchronous
indexing, which puts PDF extraction on the request path to solve a problem
nobody has — nobody uploads a document and immediately full-text searches for
it. They do immediately look at it, which is why `GET /documents/{id}` reads
Postgres and is strongly consistent.

The status field makes the lag visible rather than mysterious: `PENDING → LIVE`,
or `FAILED` with a reason.

Delivery is at-least-once, so consumer operations are idempotent — an upsert
keyed by `doc_id`, and deleting something already gone is a no-op. Elasticsearch
`version_type=external` drops a stale event that overtakes a newer one.

The costs, stated plainly: ~1 s until a document is searchable, a ~600 GB index
duplicating what Postgres already holds, replay to design around, and up to 5 s
of cache staleness.

## Caching

| | Where | TTL | Invalidation |
| --- | --- | --- | --- |
| L1 | in-process LRU | 5 s | none, it expires |
| L2 | Redis | 60 s | version counter |
| ES | shard request cache, filter bitsets | — | automatic |

L1 absorbs bursts — fifty people searching the same term in five seconds produce
one L2 lookup. Its TTL is short because it cannot be invalidated: there is no
cheap way to tell twenty pods to drop a key, so staleness is bounded instead.

Invalidation is one integer. The key embeds a version:

```
q:{tenant}:{version}:{sha1(q|filters|page|size|sort)}
```

The indexer runs `INCR inv:acme` when a document lands, so every later key is
built at `:8:` and every `:7:` key is unreachable. No `SCAN`, no enumeration.

Everything that changes the answer goes in the key and nothing else does. Drop
`page` and page 2 serves page 1; add `request_id` and the hit rate is zero; drop
`tenant` and it is a data leak.

Measured: ~90% hit rate under load, so ~350 of 1000 QPS reach Elasticsearch.

## Message queue

One topic, `doc.index.v1`, 32 partitions, keyed on `doc_id`. Tenant is a field
in the message, not topology — topic-per-tenant makes signup an infrastructure
operation and turns 32 partitions into 32,000.

The key is the opposite of the Elasticsearch routing key on purpose. Routing
co-locates a tenant; Kafka spreads work, and keying on tenant would drop a whale
tenant's corpus into one partition while 31 sit idle. `doc_id` still preserves
the only ordering that matters — `UPSERT v1 → UPSERT v2 → DELETE v3` for one
document lands in one partition, in order. Reorder those and the delete
overtakes v2, resurrecting the document. Deletes share the topic for the same
reason: a soft delete is an `UPDATE`, so splitting it out would lose that
ordering.

Failures are classified before they are retried. An encrypted PDF retried five
times reaches the same answer several minutes later, so permanent failures go
straight to `FAILED`. Transient ones move through a 30 s / 5 m / 30 m ladder on
separate topics, so the main partition commits and keeps moving; the window
outlasts a rolling Elasticsearch restart. If Elasticsearch is down entirely,
pause the consumer and let lag build rather than retrying 50,000 messages.

A failed delete is worse than a failed index — a missing document gets
complaints, a deleted-but-findable one gets silence. Alert on DLQ depth above
zero.

---
# 2 · Production readiness

Written against what is actually built. Where something exists it says so.

## 2.1 Scale — surviving 100×

100× is 1B documents and 100k searches/sec. Full derivation in
[sizing.md](resources/sizing.md).

| | Today | At 100× | Changes shape? |
| --- | --- | --- | --- |
| Elasticsearch | 6 data nodes, 24 shards, 600 GB | ~60 TB, ~200 nodes, hot/warm tiers | yes |
| Kafka | 32 partitions | partition count is the ceiling | yes |
| API | 6 pods | ~100 pods | no |
| Postgres | one primary, 15 GB | ~1.5 TB, hash partitioning | somewhat |
| Redis | 3 nodes | ~21 GB, 6-shard cluster | no |

Only two things genuinely change shape.

Elasticsearch at 60 TB does not sit on one tier. Recent documents stay on hot
nodes; older ones move to warm. Tenants above ~5M documents get their own index
via `tenants.index_group`, a column that exists today, unused, so this needs no
migration.

Kafka partitions cap consumer parallelism at one consumer each, so 32 partitions
means at most 32 indexer pods. Adding partitions rehashes `key → partition`, and
a document's events can briefly straddle old and new. Survivable — the version
guard drops stale events — but it is a planned operation. Size for the ceiling
you expect.

Everything else scales by adding replicas, because session, cache and identity
all live outside the process.

The initial backfill is the exception. Indexing 1B documents at ~1 s of
extraction CPU each is ~30 CPU-years — a migration project with its own capacity
plan, not a deployment.

## 2.2 Resilience

Built: retry with exponential backoff and jitter, a retry-topic ladder so a slow
message never blocks a partition, a DLQ, and the transactional outbox.

The useful question during an incident is not "is it up" but "what still works":

| Down | Search | Upload | Fetch |
| --- | --- | --- | --- |
| Redis | ✅ slower | ✅ | ✅ |
| Elasticsearch | ❌ | ✅ | ✅ |
| Kafka | ✅ | ✅ | ✅ |
| Indexer | ✅ | ✅ | ✅ |
| S3 | ✅ | ⚠ small only | ⚠ metadata only |
| Postgres | ✅ | ❌ | ❌ |

Two rows are design decisions rather than luck. Search survives a Postgres
outage because it never touches Postgres. Uploads survive an Elasticsearch
outage because indexing is asynchronous.

The Redis row is the one most easily broken in code — a cache client that raises
on connection failure turns an optional dependency into a required one.

**Circuit breakers.** Retries help one failing request against a healthy
service and actively hurt when the service is down. Gateway and API hops open
after 5 failures in 10 s and half-open probe after 30 s. Redis opens immediately
and falls through to Elasticsearch. The indexer pauses its consumer entirely —
the correct response to a dead search cluster is to stop consuming and let Kafka
buffer.

**Failover.** Postgres Multi-AZ with automated promotion, 60–120 s. Elasticsearch
replica shards across 3 AZs and Redis Cluster both promote in seconds. Kafka runs
replication factor 3 with `min.insync.replicas=2`, which together with `acks=all`
is what makes "we told the client 202" honest.

## 2.3 Security

**Authentication.** The gateway currently mints RS256 tokens itself; production
swaps in an OIDC provider. The verification path does not change — it already
fetches JWKS and validates `iss`, `aud`, `exp` and signature.

RS256 rather than HS256: with a shared secret, any service that can verify a
token can mint one for any tenant.

Tokens last 15 minutes. Revocation needs three layers, since a JWT cannot be
un-issued — short expiry, a `jti` denylist in Redis for emergencies, and the
tenant status check that already runs per request.

**Isolation** is covered in [§1](#multi-tenancy). Production would add Postgres
row-level security as a fifth layer, so a hand-written query in a migration is
still scoped, and per-tenant KMS keys on the S3 prefix so a bucket-policy mistake
fails closed.

**Encryption.** TLS 1.3 at the edge with HSTS; mTLS between services (currently
plaintext on the compose network). At rest: encrypted volumes for Postgres and
Elasticsearch, SSE-KMS on S3 with a per-tenant key, and backups encrypted under a
separate key in a separate account. The extracted text in `/text` needs the same
protection as `/raw` — it is the searchable content, so it is at least as
sensitive as the original.

**Secrets.** `.env` is fine for compose and unacceptable in production. Secrets
Manager or Vault, injected at runtime, rotated, with short-lived database
credentials. The RS256 private key is already never written to disk; that
property should survive the move to an external IdP.

**API surface.** Built: per-tenant rate limiting, Pydantic validation on every
input, a 20 MB body cap, RFC 7807 errors that leak nothing, presigned URLs scoped
to one object for 60 s. To add: a WAF, per-endpoint limits (search and upload
have very different cost profiles), size limits at the ALB, and dependency and
image scanning in CI.

Audit logging is missing and most enterprise buyers would require it — who read
which document, when, from where, written to storage the application cannot
rewrite.

## 2.4 Observability

Built: Prometheus metrics with RED per route, Loki for structured logs searchable
by `request_id`, Grafana over both, and correlation that survives the Kafka
boundary.

The principle is to measure what fails silently. A failed index produces a
complaint; a stuck relay, a lagging consumer and a dead-lettered delete do not.

Missing: distributed tracing. `request_id` gives causality but not per-hop
timing — you can see a request touched three services, not that Elasticsearch
took 40 ms of its 140 ms. OpenTelemetry auto-instruments Flask, SQLAlchemy, Redis
and Elasticsearch; the manual part is carrying `traceparent` across Kafka, where
auto-instrumentation stops.

Also missing: alert rules. The minimum set is SLO burn-rate on search latency and
availability, `outbox_unpublished_depth` climbing, consumer lag climbing, and DLQ
depth above zero.

## 2.5 Performance

`p95 /search = 9 ms` against a 500 ms budget, ~90% cache hit rate under load,
reproducible with `./scripts/bench.sh`.

One optimisation is worth recording for the method rather than the result. Search
p50 was 44.6 ms. My first hypothesis — HTTP connection pooling — measured wrong:
1.3 ms unpooled against 0.8 ms pooled. Bisecting hop by hop found
`keys.public_key()` re-parsing the RSA PEM on every authenticated request at
34.5 ms, more than Postgres, Redis and Elasticsearch combined. Caching the
derived key took p50 to 11.7 ms.

**Database.** Indexes match the access patterns: `(tenant, updated_at DESC)` for
listing, a partial index on the non-`LIVE` backlog, GIN with `jsonb_path_ops` for
metadata, and a partial index on unpublished outbox rows so the relay never scans
the table. At scale, PgBouncer in transaction mode and read replicas for anything
that tolerates lag — noting `GET /documents/{id}` deliberately does not, since
clients poll it immediately after writing.

**Index management.** Shard count is immutable, so resharding means a new index
and an alias swap. That is why `docs-search` is an alias from day one. Add ILM
for retention, force-merge read-only indices, watch shard size against the
10–50 GB target.

**Query.** Already: routing, `filter` rather than `must`, snippets from stored
fields, deep pagination capped at page 500. Next: `search_after` cursors end to
end, `_source` filtering, adaptive replica selection.

## 2.6 Operations

**Deployment.** Rolling updates by default — services are stateless with
readiness gates, two replicas minimum per AZ so a deploy never drops below
capacity.

Blue-green earns its place in one case: an Elasticsearch mapping change. Mappings
are largely immutable, so changing an analyzer or field type means reindexing.

```
1  create docs-v2 with the new mapping
2  backfill from Postgres + S3 /text
3  dual-write both indices while it catches up
4  compare counts, spot-check relevance
5  swap the docs-search alias — atomic, and instantly reversible
6  keep docs-v1 for a week, then drop it
```

The alias swap is the cutover, and rollback is the same command with the old
name. That is what makes the change safe to attempt on a Tuesday.

**Migrations.** `create_all()` at startup is prototype-only. Production needs
Alembic with expand/contract, because rolling deploys mean old and new code run
at once: add the nullable column and backfill, deploy code that writes both and
reads new, drop the old column a release later. Never a destructive migration in
the same deploy as the code that depends on it.

**Backup and recovery.**

| | Method | RPO | RTO |
| --- | --- | --- | --- |
| Postgres | WAL archiving, PITR | < 5 min | ~30 min |
| S3 | versioning + cross-region replication | ~15 min | minutes |
| Elasticsearch | snapshots, or rebuild | n/a | hours |
| Redis | none needed | n/a | instant |

The last two rows are the interesting ones. Elasticsearch does not strictly need
backups — it is derived from Postgres and S3, so recovery is a reindex and
snapshots only optimise restore time. Redis holds caches and rate-limit counters;
losing it costs a cold cache for a minute. Backup strategy follows from
architecture rather than being bolted on.

Restores need testing on a schedule. An untested backup is a hypothesis.

**Runbooks.** The metrics already point at the failure modes that need one:
outbox depth climbing (relay stuck), consumer lag climbing (indexer behind), DLQ
non-empty (poisoned message), `FAILED` backlog growing (extraction breaking on a
document class), cache hit ratio collapsing.

## 2.7 SLA — 99.95%

21.9 minutes a month. Deployments count, so anything short of zero-downtime eats
the budget outright.

Serial dependencies multiply:

```
gateway        99.99%
api            99.99%
Elasticsearch  99.95%
               ───────
composite      99.93%   ← under target
```

Elasticsearch is the binding constraint, so that is where redundancy spend goes:
more replica shards, three AZs, dedicated master nodes so a data-node problem
cannot take out cluster coordination.

Redis is deliberately absent from that chain. It degrades to a cache miss rather
than an error, which is worth more than any amount of Redis redundancy.

The SLI is successful searches over total searches, excluding 4xx, counting a
slow-but-successful search as a success. A latency SLO sits alongside: 95% under
500 ms. Writes get a separate objective — a `202` that eventually indexes is a
success even if the indexer was briefly behind.

21.9 minutes is a budget, not a target. Burn-rate alerting over one hour and six
hours says whether an incident threatens the month. Healthy budget, ship;
nearly spent, freeze risky changes. That is the mechanism that makes the number
mean something.

## 2.8 Cost

Roughly, at the brief's scale on AWS:

| | Monthly |
| --- | --- |
| Elasticsearch — 6 data + 3 master + 2 coord | ~$2,400 |
| Postgres — Multi-AZ, 250 GB | ~$700 |
| Kafka (MSK) — 3 brokers | ~$450 |
| Redis (ElastiCache) — 3 nodes | ~$250 |
| Compute — API + indexer | ~$500 |
| S3 — 100 GB plus requests | ~$10 |
| **Total** | **~$4,300** |

Elasticsearch is 56%, so that is where optimisation pays.

Cache harder — the hit rate is ~90%, and every point above that removes traffic
from the most expensive component. Tier hot/warm, since most searches touch
recent documents. Run the indexer on spot instances: it is interruption-tolerant
by construction, because Kafka redelivers anything a killed worker did not
commit, which is roughly 70% off the one workload that can take it.

Right-size on measurement. The shard count assumes 50 KB of extracted text per
document. At 5 KB the cluster is a third the size, and shard count cannot be
changed later without a reindex — so measure a 10k-document sample first.

S3 is a rounding error: 100 GB of documents costs about $2.

---
# 3 · Experience showcase

## A similar distributed system I've built

I designed and implemented the task-management service that runs internal team
communication for our organisation — a [Front](https://front.com/)-style shared
workspace where the searchable unit is a task's JSON payload rather than a
document. It serves two quite different read patterns from one system: a
per-tenant view scoped to a single customer, and a cross-tenant **cockpit**
where internal users search, filter and group across every tenant at once while
still performing full CRUD.
At roughly **1,700 tenants averaging 1,000 tasks each — ~1.7M tasks and growing
daily** — the cockpit's search, grouping and filtering could not be served from
the operational store, which is what drove the split between the system of
record and a dedicated search index.

The write path separates durability from searchability. A create, update or
delete commits to **NDB** first, which remains the source of truth, and then
publishes to a queue — Kafka, or GCP Task Queue depending on the environment.
An **indexer service** consumes from it, projects the task JSON into
**Elasticsearch**, and writes the indexed status back to NDB so the UI can show
where a task actually is. Intermittent failures retry with exponential backoff
and fall through to a DLQ rather than stalling the consumer.

## A performance optimisation with significant impact

In that same service, every create, update and delete was paying for its own
search indexing. The write path did the durable NDB commit *and* the
Elasticsearch projection before returning, so users absorbed the cost of a
subsystem they were not using at that moment — and the slowest, least reliable
hop in the request was the one that mattered least to the caller. Indexing
latency also varied with Elasticsearch load, which meant a slow cluster showed
up as slow task operations, and an Elasticsearch problem could fail a write
that had already durably committed. I split them: task operations now commit to
NDB, publish to the queue, and return as soon as the write is durable, leaving
the indexer to project into Elasticsearch asynchronously. **Task-operation
latency dropped ~30%**, and the write path stopped inheriting Elasticsearch's
availability. The deliberate trade was that cockpit search became eventually
consistent — acceptable because the status write-back makes the lag observable
rather than mysterious, and nobody creates a task and immediately searches for
it.

Three further changes targeted the read side, where 1.7M tasks made the cockpit
the expensive surface. **Caching** in front of the cockpit's repeated filter and
group queries removed most of the duplicate work, since internal users return to
the same handful of views throughout a shift. **Pagination** moved off deep
`from`/`size` paging to cursor-based `search_after` — at that row count, deep
paging makes every shard collect and discard everything before the requested
offset, so the cost grows with page depth for no benefit. And rather than
indexing the whole task record, the indexer writes a **projection**: only the
fields the cockpit actually searches, filters, groups and renders. That cut
index size and per-write cost together, and made each hit cheaper to return.

<!--
    Still to write:

    3. A critical production incident you resolved
       — tasks not syncing to Elasticsearch (intermittent indexer/Kafka failure).
         Needs: how it was detected, how it was diagnosed, how the out-of-sync
         tasks were recovered, and what changed so it could not recur.

    4. An architectural decision that balanced competing concerns
       — needs the option that was rejected, and what it would have cost.
-->

---

## Assumptions

The brief leaves these open; each is a decision, not a given.

| | Assumed | Why it matters |
| --- | --- | --- |
| Document size | real files up to ~100 MB | drives the 256 KB / 5 MB storage split and the extraction stage |
| Formats | pdf, docx, txt, md, csv, html — **no scanned images** | OCR is out of scope; a PDF with no text layer becomes `FAILED: needs OCR` |
| Extracted text | ~50 KB per document | the shard-count input; the number most worth verifying against real data |
| Search semantics | lexical (BM25), not semantic | the brief says full-text search with relevance ranking |
| Read-your-writes | not required for search | permits async indexing; `GET /documents/{id}` *is* strongly consistent |
| Tenant scale | thousands of tenants, skewed | one routed index, with a promotion path for whales |
| Users per tenant | all users see all tenant documents | no per-user ACL, so the cache key needs no user dimension |
| Onboarding | out of scope — tenants and users are seeded | signup would be an admin surface, not a search concern |

## What is not built

Named plainly rather than left to be discovered: no idempotency on upload, no
integration tests against real infrastructure, no OCR or passage chunking, no
distributed tracing, no alert rules, no audit log, and no per-tenant label on
`documents_by_status` — so Grafana counts across all tenants while the UI shows
one. Presigned direct-to-S3 upload is designed and diagrammed but the prototype
streams through the API instead.

There are 229 unit tests (`pytest`, ~4 s, no infrastructure required) covering
the invariants this document argues for — tenant isolation in the cache key,
`filter`-not-`must`, the indexer's `PENDING` gate, S3-before-row ordering, the
HS256 confusion attack, and the OCR guard. Each was verified by mutation:
breaking the invariant makes a test fail. See the [README](README.md#tests).
