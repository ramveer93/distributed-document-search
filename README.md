# Deeprunner

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white) ![Flask](https://img.shields.io/badge/Flask-000000?style=flat&logo=flask&logoColor=white) ![React](https://img.shields.io/badge/React-61DAFB?style=flat&logo=react&logoColor=black) ![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat&logo=typescript&logoColor=white) ![Vite](https://img.shields.io/badge/Vite-646CFF?style=flat&logo=vite&logoColor=white) ![Elasticsearch](https://img.shields.io/badge/Elasticsearch-005571?style=flat&logo=elasticsearch&logoColor=white) ![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?style=flat&logo=postgresql&logoColor=white) ![Apache Kafka](https://img.shields.io/badge/Apache%20Kafka-231F20?style=flat&logo=apachekafka&logoColor=white) ![Redis](https://img.shields.io/badge/Redis-FF4438?style=flat&logo=redis&logoColor=white) ![Docker](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=white) ![MinIO](https://img.shields.io/badge/MinIO-C72E49?style=flat&logo=minio&logoColor=white) ![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?style=flat&logo=prometheus&logoColor=white) ![Grafana](https://img.shields.io/badge/Grafana-F46800?style=flat&logo=grafana&logoColor=white)

A multi-tenant document search service. Upload a PDF, DOCX or HTML file; the
text is extracted asynchronously and becomes full-text searchable with
relevance ranking, highlighting and facets — scoped to your tenant and nobody
else's.

Built against these targets: **10M+ documents · 1000+ searches/sec · p95 under
500 ms · tenant isolation · horizontal scale.**

Measured on this prototype: **p95 `/search` = 9 ms**, cache hit ~90%.

> **The design document is [DESIGN.md](DESIGN.md)** — architecture and data
> flows, production readiness (scale, resilience, security, operations, the
> 99.95% SLA arithmetic, cost), and the trade-offs behind each decision.
> This README is how to run and verify the thing.

---

## Walkthroughs

Three recorded walkthroughs — the two architecture ones talk through the
diagrams, the third is the running application.

| | | |
| :---: | --- | --- |
| [![Watch the indexing walkthrough](https://img.shields.io/badge/%E2%96%B6%20Watch-Loom-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/5a0ed4dddc584da3822e1221f36f6a6a) | **[Indexing architecture](https://www.loom.com/share/5a0ed4dddc584da3822e1221f36f6a6a)** — the write path | upload → S3 → outbox → Kafka → extraction → Elasticsearch |
| [![Watch the search walkthrough](https://img.shields.io/badge/%E2%96%B6%20Watch-Loom-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/eabddd520637489ea87d590c932dc4b2) | **[Search architecture](https://www.loom.com/share/eabddd520637489ea87d590c932dc4b2)** — the read path | two cache levels, routing, the tenant filter, the latency budget |
| [![Watch the application demo](https://img.shields.io/badge/%E2%96%B6%20Watch-Loom-625DF5?style=for-the-badge&logo=loom&logoColor=white)](https://www.loom.com/share/b500cedbf4aa4f3f84798247edb2cd8e) | **[Application demo](https://www.loom.com/share/b500cedbf4aa4f3f84798247edb2cd8e)** — the whole thing running | upload, state transitions, search, tenant isolation, observability |

---

## Quick start

```bash
git clone <repo> && cd deeprunner
cp .env.example .env          # dev values, no real secrets
docker compose up -d          # ~2 min on first pull
```

Open **http://localhost:3001** and sign in.

| Email | Tenant | Password | |
| --- | --- | --- | --- |
| `alice@acme.com` | acme | `demo` | 600 req/min |
| `bob@globex.com` | globex | `demo` | 300 req/min — a second tenant, for proving isolation |
| `carol@initech.com` | initech | `demo` | **suspended** — login returns 403 |

Everything else:

| | | |
| --- | --- | --- |
| App | http://localhost:3001 | |
| Grafana | http://localhost:3000/d/deeprunner/deeprunner | no login |
| Prometheus | http://localhost:9090 | |
| MinIO (S3) | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Elasticsearch | http://localhost:9200/_cat/indices?v | |
| Kafka UI | http://localhost:8090 | `docker compose --profile devtools up -d` |

---

## Try it in five steps

All of it is recorded in the [application demo](https://www.loom.com/share/b500cedbf4aa4f3f84798247edb2cd8e) if you would rather
watch than install.

1. **Upload a PDF** — *Add document*. The detail page shows three stages:
   `Stored → Queued for indexing → Searchable`. Each reads a real signal (the
   committed row, `outbox.published_at`, then `status = LIVE`), not a timer.

2. **Search a phrase from inside the file.** The chip says `MISS`. Search the
   same thing again → `HIT`, and the latency drops from ~15 ms to single
   digits.

3. **Copy the Document ID** off the detail page, then find the same UUID in
   MinIO (`acme/<id>/raw` and `/text`), in the Kafka message key, and in
   Elasticsearch (`_id` = `acme:<id>`).

4. **Prove isolation.** Sign out, sign in as `bob@globex.com`, run the same
   search → zero results. Paste one of alice's document URLs → **404, not
   403** (a 403 would confirm the document exists).

5. **Break something on purpose.** Upload a scanned PDF with no text layer →
   `FAILED: no text layer — this document needs OCR`, and the stepper stops at
   the stage that broke.

```bash
./scripts/smoke.sh     # 19 end-to-end assertions
./scripts/bench.sh     # search p50/p95, split by cache hit and miss
./scripts/loadgen.sh   # traffic, so the dashboards have something to show
```

---

## API

The full request set is a Postman collection:

**[`postman/deeprunner.postman_collection.json`](postman/deeprunner.postman_collection.json)**

Import it, run **Auth → Log in (acme)** first — it stores the JWT in a
collection variable every other request uses — then work through the folders.

| Folder | |
| --- | --- |
| **Auth** | login, the suspended tenant, a bad password, JWKS |
| **Documents** | index text, index >256 KB (goes to S3), upload a file, get, list, download, delete |
| **Search** | ranked and faceted, fuzzy off, deep-page rejection |
| **Tenant isolation** | globex reading acme's document, and searching for it |
| **Health** | liveness and dependency status |

Every request carries assertions, so the collection doubles as an end-to-end
test. Run it headless:

```bash
npx newman run postman/deeprunner.postman_collection.json
```

```
requests    21
assertions  29        0 failed
duration    1.3s
```

`baseUrl` defaults to `http://localhost:3001/api`, the frontend origin that
proxies to the gateway. Point it at `http://localhost:8080` to hit the gateway
directly.

Endpoints, for reference — everything except `/health` needs
`Authorization: Bearer <jwt>`, and **the tenant is never sent by the client**;
it is read from the token claim.

| Method | Path | |
| --- | --- | --- |
| `POST` | `/auth/token` | log in, 15-minute RS256 JWT |
| `GET` | `/search` | full-text, ranked, highlighted, faceted |
| `POST` | `/documents` | index — JSON body or `multipart/form-data` file |
| `GET` | `/documents` | list, paginated, filterable by status |
| `GET` | `/documents/{id}` | detail, including indexing progress |
| `GET` | `/documents/{id}/raw` | 302 to a presigned S3 URL, 60s |
| `DELETE` | `/documents/{id}` | soft delete |
| `GET` | `/health` | liveness · `/health/detail` for dependencies |

Errors are RFC 7807 `application/problem+json` and always carry `trace_id`.
A cross-tenant read returns **404, not 403** — a 403 would confirm the
document exists.

### Rate limiting

Per tenant, enforced **at the gateway** — an over-quota tenant is rejected
before it burns any API, Elasticsearch or Postgres capacity. Limits come from
the tenant row, so changing a customer's plan is an `UPDATE`, not a deploy:

| Tenant | Limit |
| --- | --- |
| acme | 600 req/min |
| globex | 300 req/min |

Every authenticated response carries the current allowance:

```
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 597
```

Over the limit is a `429` that says when to come back — without `Retry-After`,
most clients simply retry immediately and make the problem worse:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 37
X-RateLimit-Remaining: 0

{ "type": "/errors/rate-limited", "title": "Rate limit exceeded",
  "status": 429, "detail": "600 requests/min exceeded", "trace_id": "r-…" }
```

A fixed window — one `INCR` plus one `EXPIRE` on `rl:{tenant}:{minute}`. A
sliding window is more accurate but needs a sorted set per tenant, which is
not worth it for fairness limiting. Rejected requests still count, so hammering
past the limit cannot reset the window.

`./scripts/smoke.sh` asserts the 429; `test_rate_limit.py` covers the window,
the expiry and per-tenant isolation.

---

## Architecture

The full treatment — diagrams, data flows, trade-offs — is in
**[DESIGN.md](DESIGN.md)**. Six decisions worth knowing up front:

**Writes are asynchronous, and say so.** `POST /documents` returns **202
PENDING**, not 201. The document is durable but not yet searchable. Claiming
201 would promise something the write path hasn't delivered.

**The document row and its outbox row commit in one transaction.** No dual
write. Kafka being down means lag, never a lost document.

**Postgres is the source of truth; Elasticsearch is derived and disposable.**
Lose the index and you replay from Postgres + S3.

**Isolation has three layers** — tenant minted from the JWT claim (never a
client header), a mandatory `term` filter injected by the repository layer so
no caller can omit it, and tenant-prefixed cache keys. A cross-tenant read
returns **404, not 403**.

**Kafka is keyed on `doc_id`, not tenant.** Ordering only matters per
document — an `UPSERT` must not overtake the `DELETE` behind it. Keying on
tenant would drop a busy customer's whole corpus into one partition.

**Elasticsearch routes by tenant**, so a search touches one shard instead of
all of them. That is the main reason the latency budget holds.

### The flows in detail

| Flow | Diagram | Written up | Walkthrough |
| --- | --- | --- | --- |
| Write path — upload to searchable | [index.png](resources/index.png) | [index-flow.md](resources/index-flow.md) | 🎥 [video](https://www.loom.com/share/5a0ed4dddc584da3822e1221f36f6a6a) |
| Read path — search | [search.png](resources/search.png) | [search-flow.md](resources/search-flow.md) | 🎥 [video](https://www.loom.com/share/eabddd520637489ea87d590c932dc4b2) |
| Fetch, download, delete | [document.png](resources/document.png) | [DESIGN.md](DESIGN.md#fetch-download-and-delete) | — |
| Sharding and capacity | — | [sizing.md](resources/sizing.md) | — |
| Design document | — | [DESIGN.md](DESIGN.md) | — |

The assumptions everything rests on — document size, formats, extracted-text
volume, consistency expectations — are listed in
[DESIGN.md](DESIGN.md#assumptions).

---

## Layout

```
backend/
  common/            config · SQLAlchemy models · clients · auth · middleware
                     problem (RFC 7807) · observability
  gateway/           auth, rate limit, request-id, proxy      → container
  search_service/    ┐
  document_service/  ├ blueprints, mounted by backend/main.py → container
  index_service/     ┘
  indexer/           outbox relay + Kafka consumer + extraction → container
frontend/            React + TypeScript + Vite, nginx serves it and proxies /api
ops/                 prometheus, grafana, loki config
scripts/             smoke · bench · loadgen · es · pg
resources/           design docs and diagrams
```

Each service uses the same four layers: `routes` (URL mapping) → `handlers`
(HTTP marshalling) → `managers` (business logic) → `repositories` (data
access). No business logic in a handler; no SQL outside a repository.

**Three deployables, not five.** Search, document and index are separate
packages but one process — ingest is roughly a tenth of search traffic, so
splitting them would buy operational cost with no scaling benefit. The indexer
is separate because it is a queue consumer: it scales on lag, not request rate.

---

## Prototype vs production

Every row is a deliberate scope decision, not an unfinished one.

| | Prototype | Production |
| --- | --- | --- |
| Change capture | outbox table + polling relay | Debezium reads the WAL; the outbox table disappears |
| Upload | `multipart` through the API, 20 MB cap | presigned `:negotiate` → client PUTs to S3 → `:commit` |
| `AWAITING_UPLOAD` status | unreachable — only exists with presigned upload | the state between negotiate and commit |
| Download | **presigned 302, already built** | same |
| Scanned PDFs | `FAILED: needs OCR` | OCR on a separate worker pool and topic |
| Long documents | indexed whole | chunked into passages, collapsed by `doc_id` |
| Shards | 2 | 24 — see [sizing.md](resources/sizing.md) |
| Migrations | `create_all()` at startup | Alembic |
| Key management | RS256 keypair generated in-process | an OIDC provider (Cognito/Auth0/Keycloak) |
| Tracing | `request_id` correlated in logs, searchable in Grafana | OpenTelemetry spans, with `traceparent` carried across Kafka |

---

## Observability

Grafana at http://localhost:3000/d/deeprunner/deeprunner, no login. Metrics come
from Prometheus, logs from Loki via Alloy. What each metric is for, and why
`request_id` is a log field rather than a Prometheus label, is in
[DESIGN.md](DESIGN.md#24-observability).

One request id flows gateway → api → outbox row → Kafka message → indexer, so a
single grep follows a document from HTTP request to indexed:

```bash
docker compose logs gateway api indexer | grep r-f3219a8948b0
```

In Grafana, paste it into the **Request ID** box, or query Loki directly:

```
{job="deeprunner"} | json | request_id = "r-f3219a8948b0"
```

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest                                # 229 tests, ~4s, nothing needs to be running
pytest backend/tests/test_consumer.py -v
```

Every external dependency is an in-process double, so the suite runs with no
docker and no network. What is covered is the logic where a bug is silent —
cache keys, the tenant filter, the indexer's status gate, extraction edge
cases — rather than the plumbing that fails loudly on first use.

| | |
| --- | --- |
| `test_search_cache.py` | tenant isolation in the cache key, version invalidation, L1/L2 |
| `test_search_query.py` | routing, `filter`-not-`must`, boosting, facet namespacing |
| `test_auth.py` | RS256, the HS256 confusion attack, expiry, audience, login |
| `test_consumer.py` | the PENDING gate, version guard, delete paths, invalidation |
| `test_extraction.py` | magic-byte sniffing, the OCR guard, corrupt files |
| `test_write_path.py` | the 256 KB split, S3-before-row, doc_id/key derivation |
| `test_read_path.py` | 404-not-403, malformed ids, progress states |
| `test_gateway_proxy.py` | header allowlist, spoofed identity, trace propagation |
| `test_rate_limit.py` | per-tenant windows, expiry, `Retry-After` |
| `test_errors.py` | RFC 7807 shape, no internals in a 500 |

The suite was checked by mutation: breaking each design invariant in turn —
dropping the tenant from the cache key, allowing HS256, removing the `PENDING`
gate, writing the row before the S3 bytes — makes tests fail rather than pass
quietly.

---

## Verifying it works

```bash
./scripts/smoke.sh    # 19 assertions: auth, isolation, cache, delete, rate limit
./scripts/bench.sh    # p50/p95 for /search, split by cache state
./scripts/es.sh       # indexes, shards, docs per tenant
./scripts/pg.sh       # tenants, storage split, outbox backlog
./scripts/pg.sh <id>  # one document and its outbox entries
```

A measured optimisation, reproducible with `bench.sh`: `keys.public_key()` was
re-parsing the RSA PEM on **every authenticated request**, costing 34.5 ms of a
44.6 ms p50. Caching the derived key took p50 to **11.7 ms** — a 74% cut. How
it was found, including the first hypothesis that measured wrong, is in
[DESIGN.md](DESIGN.md#25-performance).

---

## Not built

The prototype-versus-production table above covers the deliberate scope cuts.
The honest gap list — idempotency, integration tests, tracing, alert rules,
audit logging, per-tenant metric labels — is in
[DESIGN.md](DESIGN.md#what-is-not-built).

---
