# Sharding Strategy & Capacity

Targets from the brief: **10M+ documents · 1000+ searches/sec · p95 < 500 ms · 100× growth**

**Assumption:** ~50 KB of extracted text per document (a ~20 page file). The brief does not state document size. Everything below scales off this one number, so it is the first thing to measure, not guess.

---

## Sharding strategy

**One index, N shards, all tenants share them.**

```
shard = hash(tenant_id) % N
```

Every document for a tenant lands on the same shard, so a search with `routing=tenant_id` touches **one shard instead of all of them**. Different tenants hash to different shards, so load still spreads across the cluster.

| Strategy | Shards at 1000 tenants | Query cost | Verdict |
| --- | --- | --- | --- |
| Index per tenant | ~2000 | 1 shard | cluster dies — shards cost heap |
| **Shared + routing** | **24** | **1 shard** | **chosen** |
| Shared, no routing | 24 | all shards | correct but 24× the work |

**Cost:** a whale tenant unbalances its shard. Two escape hatches, no code change:

- `index.routing_partition_size = 3` — spread one tenant over 3 shards instead of 1
- `tenants.index_group` — promote a whale to its own index

**Shard count is immutable.** `hash % N` changes if `N` does, so every document would be on the wrong shard. Resharding means: build a new index, reindex, swap the alias (`docs-search` → `docs-v2`). Pick with room to grow.

---

## Elasticsearch

```
10M docs × 50 KB text                 =  500 GB text
× 1.2  (inverted index + doc values)  =  600 GB primary
× 2    (1 replica)                    =  1.2 TB on disk
÷ 0.7  (disk watermark + merge room)  =  ~1.7 TB provisioned

shards:  600 GB ÷ 30 GB target        =  20  →  24  (4 per node, even spread)
```

| Role | Count | Spec |
| --- | --- | --- |
| Data | **6** | 64 GB RAM (31 GB heap), 1 TB NVMe |
| Master | 3 | small, quorum only |
| Coordinating | 2 | optional, absorbs fan-out |

**Query load is not the constraint.** 1000 QPS − 65% cache = ~350 QPS at ES, spread over 24 shards ≈ 15 QPS per shard. Replicas double read capacity. **Storage sizes this cluster, not throughput.**

If p95 misses target, add data nodes rather than bigger ones — you want more aggregate page cache, not a bigger heap (31 GB is the hard ceiling for compressed pointers).

---

## Redis

Working set:

```
query cache   1000 QPS × 60 s TTL      = 60,000 requests per window
              × 35% miss rate          = ~21,000 distinct entries
              × ~10 KB per response    = ~210 MB
rate limits   1000 tenants × 1 key/min = negligible
inv counters  1000 integers            = negligible
                                         ─────────
                                         ~250 MB
```

Throughput: ~3 ops per search (`GET inv`, `GET q:`, `INCR rl:`) → **~3,000 ops/sec**. A single Redis node does 50–100k.

| | |
| --- | --- |
| Nodes | **3** (1 primary + 2 replicas) |
| Size | 4–8 GB each |

**Redis is sized for availability, not capacity.** 250 MB and 3k ops/sec would fit on a laptop; the three nodes exist so a failover doesn't take search down.

---

## Search Service

CPU per request is small — the service mostly waits on Elasticsearch.

```
~2 ms CPU/request  (parse, hash key, serialize 10 KB)
× 1000 QPS         = 2 cores of actual work
× 2 headroom       = 4 cores

concurrency (Little's Law):
  avg latency = 0.35 × 140 ms + 0.65 × 10 ms = ~55 ms
  in flight   = 1000 × 0.055 = ~55 concurrent requests
```

| | |
| --- | --- |
| Pods | **6** (2 per AZ × 3 AZs) |
| Spec | 2 vCPU / 2 GB |
| Autoscale | HPA 6 → 30 on p95 latency |

Six is driven by AZ spread and rolling-deploy headroom, not CPU — 55 concurrent requests over 6 pods is ~9 each, trivial for async I/O. Six pods also keep six warm L1 caches.

---

## Ingest & Indexer

**Ingest: 3 pods.** Write traffic is ~116/sec and each request is one insert; the 256 KB–5 MB tier streams bodies through, so memory stays flat.

**Indexer is the interesting one** — and it's dominated by the *initial load*, not steady state:

| Phase | Rate | Extraction CPU | Pods |
| --- | --- | --- | --- |
| Initial load (10M in 24 h) | 116/sec | ~1 s per PDF → **~58 cores** | **~30**, temporary |
| Steady state (~1% churn/day) | ~2/sec | ~2 cores | **4**, KEDA on lag |

Scale up for the migration, scale back down after. Sizing the indexer for steady state and then attempting a bulk load is how backfills end up taking three weeks.

Spec: 4 vCPU / 4 GB, `ephemeral-storage: 4Gi` for extraction temp files.

---

## At 100× (1B docs, 100k QPS)

| | Change |
| --- | --- |
| Elasticsearch | 60 TB → hot/warm tiering, whales on dedicated indices, ~200 data nodes |
| Redis | cache scales with *distinct queries*, not corpus → ~21 GB, 6-shard cluster |
| Search Service | 200 cores → ~100 pods, same architecture |
| Indexer | unchanged shape, more replicas |

Only Elasticsearch changes shape. Everything else is the same design with a bigger number — which is the point of keeping state out of the services.

---

## Measure before you commit

Shard count can't be changed later, so derive it rather than guess:

```
1. index 10,000 representative documents
2. GET _cat/indices?v   →  read store.size
3. × 1000               →  projected total
4. ÷ 30 GB              →  shard count, rounded to a multiple of data nodes
```

Sensitivity to the one assumption:

| Avg extracted text | Index size | Shards |
| --- | --- | --- |
| 5 KB | 65 GB | 3 |
| 10 KB | 130 GB | 6 |
| **50 KB** | **600 GB** | **24** |
| 200 KB | 2.4 TB | 80 |

Ten minutes of measurement replaces the whole table.
