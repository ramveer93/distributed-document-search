# Index Flow — `POST /documents`

Diagram: [index-flow.excalidraw](index-flow.excalidraw)

**Ingest owns every threshold. The client is told what to do, it never chooses.**

| Threshold | Decides |
| --- | --- |
| 256 KB | body in the Postgres row, or in S3 |
| 5 MB | bytes through the API, or presigned direct to S3 |
| 100 MB | single presigned PUT, or S3 multipart |

---

## Lane A — ≤ 5 MB, one call

1. `POST /documents`, bytes in the body
2. Ingest validates auth + tenant
3. **≤ 256 KB** → body in the row · **> 256 KB** → S3 first, *then* the row with `s3_key`
4. Row is born `PENDING` — the bytes already exist
5. `202`

## Lane B — > 5 MB, three calls

1. `POST /documents:negotiate` `{filename, size}`
2. `> 100 MB` → `CreateMultipartUpload` → `upload_id` *(the only S3 call here)*
3. Presign URL(s) — local HMAC, no network call
4. `INSERT` row, status = `AWAITING_UPLOAD`
5. Return `doc_id` + URL(s)
6. Client PUTs bytes **direct to S3** — parts in parallel if multipart
7. `POST /documents/{id}:commit` + part ETags
8. `CompleteMultipartUpload` if needed, then `HEAD` to verify
9. Status → `PENDING` ← **the commit point**
10. `202`

## Async — identical for both lanes

11. Postgres WAL → Debezium → Kafka *(no outbox table)*
12. Indexer consumes, **acts on `PENDING` only**
13. Get the body → extract → index *(see below)*
14. `PUT /text` to S3
15. Index into Elasticsearch
16. `INCR inv:{tenant}` → tenant query cache voided
17. Status = `LIVE`

---

## Kafka

| | |
| --- | --- |
| Topics | `doc.index.v1` + a retry ladder + `doc.index.dlq` — see [Failure handling](#failure-handling) |
| Partitions | 32 |
| Key | `doc_id` |

**One topic for every tenant.** Tenant is a *field in the message*, not topology. Topic-per-tenant would make signup an infrastructure operation (create topic → wait for propagation → resubscribe every consumer) and turn 32 partitions into 32,000.

**The key is `doc_id`, not `tenant_id`** — deliberately the opposite of Elasticsearch:

| | Key | Goal |
| --- | --- | --- |
| ES routing | `tenant_id` | **co-locate** — one tenant's docs on one shard |
| Kafka key | `doc_id` | **spread** — work fans out across all partitions |

Kafka orders only *within* a partition, and the only ordering that matters is per document:

```
doc-1:   UPSERT v1  →  UPSERT v2  →  DELETE v3
```

Reorder those and the DELETE lands before v2 — a resurrected document. Keying on `doc_id` puts all three in one partition, in order. Nothing needs cross-document ordering.

`tenant_id` as the key would drop a whale tenant's entire corpus into one partition: head-of-line blocking while 31 sit idle. Because the key is `doc_id`, that load already spreads.

### Deletes share the topic

No separate delete topic:

1. **Ordering** — separate topics mean separate partitions mean no ordering between an UPSERT and a DELETE for the same document. Exactly the bug the key prevents.
2. **A soft delete is an `UPDATE`** — Debezium emits `op: u` with `status: DELETED`. Same event shape, same stream, nothing to split.

The Indexer branches on the field:

```
status = PENDING   →  index it
status = DELETED   →  delete from Elasticsearch by doc_id
anything else      →  skip   (AWAITING_UPLOAD, LIVE, FAILED)
```

That last line is also what stops the `status = LIVE` write-back from looping back through Debezium.

Hard deletes from the retention job arrive as `op: d`; the Indexer ignores them, since Elasticsearch was already cleaned when status flipped to `DELETED`.

### Why 32 partitions

It caps consumer parallelism — one partition per consumer in a group, so 32 is the ceiling on Indexer pods. The backfill sizing wanted ~30, so 32 with a little headroom.

Raising it later rehashes `key → partition`, so a document's events could briefly straddle old and new partitions. Cheaper to size it once.

---

## Inside the Indexer

```
/text already in S3?     → use it, skip extraction        (reindex path)
else                     → stream /raw to a temp file on local disk
sniff magic bytes        → %PDF, PK\x03\x04 — never Content-Type
extract page by page     → pypdf · python-docx · plain decode
PDF, pages, no text      → FAILED "needs OCR", not an empty document
                           temp file deleted on exit, exception or not
```

| In scope | Out of scope |
| --- | --- |
| txt, md, csv, html | OCR |
| pdf (text layer) | passage chunking |
| docx | ES attachment processor |

**No extraction size cap.** A 100 MB PDF runs the same code in the same lane — streaming to disk keeps memory flat either way. Only the consumer config differs.

**Why not the ES attachment processor:** Tika would parse on the data nodes, burning CPU that has to answer searches in under 500 ms.

---

## Status

```
                   :commit          indexed
AWAITING_UPLOAD ───────────▶ PENDING ────────▶ LIVE
       │                        │
       │ no commit in 24 h      ├─▶ FAILED  unsupported type
       ▼                        ├─▶ FAILED  no text layer (needs OCR)
   ABANDONED                    └─▶ FAILED  retries exhausted
```

Lane A rows are born `PENDING` — nothing to wait for.

---

## Failure handling

### Classify before retrying

| | Examples | Action |
| --- | --- | --- |
| **Transient** | ES 503, network timeout, S3 throttle, connection reset | retry with backoff |
| **Permanent** | encrypted PDF, unsupported type, mapping conflict | straight to `FAILED`, **no retries** |

Retrying an encrypted PDF five times reaches the same answer minutes later, with everything behind it delayed. `FAILED: needs OCR` is the *outcome*, not an error.

### Retry off the main partition

Retrying in place holds the partition — nothing behind the message moves, and exceeding `max.poll.interval.ms` gets the consumer evicted into a rebalance loop.

```
doc.index.v1 ──fail──▶ retry.30s ──fail──▶ retry.5m ──fail──▶ retry.30m ──fail──▶ dlq
```

Each retry topic has its own consumer that waits before processing, so the main partition commits and keeps moving.

```
in-process     2 attempts, 200 ms + jitter      network blips
retry topics   30 s → 5 m → 30 m (+ jitter)     service recovering
DLQ            after ~35 min total
```

The window is sized to outlast a rolling Elasticsearch restart. Three fast retries over a few seconds would not.

**Jitter is not optional** — without it every failed message retries at the same instant and hammers the service that is trying to recover.

### Total outage: pause, don't retry

If Elasticsearch is down, pushing 50,000 messages through the ladder is pointless. Detect it, pause the consumer, let lag build — Kafka is a buffer, that is its job — and resume on health check.

Circuit breaker for total outages. The retry ladder is for *individual* messages failing against a *healthy* service.

### A failed delete is worse than a failed index

```
index fails    →  document not searchable          →  visible, someone complains
delete fails   →  deleted document still findable  →  nobody notices
```

The second is a privacy incident wearing a queue metric's clothing. So:

- alert on **DLQ depth > 0**, not > 100
- tag delete failures separately and page on them
- nightly reconciliation — for every `DELETED` row, confirm it is gone from the index

### Why retries are safe at all

Both operations are **idempotent**: indexing is an upsert by `doc_id` guarded by version, and deleting something already gone is a no-op. Without that, retrying would corrupt rather than heal.

---

## S3 layout

```
{tenant}/{doc_id}/raw     original bytes, immutable
{tenant}/{doc_id}/text    extracted text, derived, rebuildable
```

Real S3 API calls — **presigning is not one of them** (local HMAC, zero network):

| Call | When |
| --- | --- |
| `CreateMultipartUpload` | `:negotiate`, > 100 MB only |
| `CompleteMultipartUpload` | `:commit` |
| `HEAD` | `:commit`, verify it landed |
| `GET /raw` | Indexer, when the row has an `s3_key` |
| `PUT /text` | Indexer, after extraction |

---

## Config that matters

```
batch by BYTES, not count        a 100 MB doc becomes a batch of one
max.poll.interval.ms = 900000    else slow extraction looks like a dead
                                 consumer → rebalance loop, nothing finishes
ephemeral-storage: 4Gi           concurrent temp files, or the pod is evicted
AbortIncompleteMultipartUpload   7 days — abandoned parts are billed and
                                 invisible in the object listing
sweep AWAITING_UPLOAD > 24 h     → ABANDONED
```

---

## The three rules

1. **S3 always lands before the row** — a row can never point at bytes that aren't there. The reverse leaves an orphan blob, which is harmless.
2. **The Indexer only touches `PENDING`** — safe to write the row 60s early, and the `LIVE` write-back can't loop back through Debezium.
3. **`/raw` is immutable, `/text` is derived** — a reindex reads `/text` and never re-parses 10M PDFs.

---

## Assumptions

The assignment does not specify document size or format. Assumed: real files (pdf, docx, txt) up to ~100 MB, no scanned documents. This drives the storage split and the extraction step.
