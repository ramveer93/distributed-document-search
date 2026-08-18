# Elasticsearch queries

Kibana Console syntax: `METHOD /path` then an optional JSON body.
For curl, prefix with `curl -s -X METHOD "localhost:9200..." -H 'Content-Type: application/json' -d '…'`.

**Routing matters.** Documents are indexed with `routing=<tenant>`, so anything
addressing a single document by id must pass the same value — otherwise
Elasticsearch looks on the wrong shard and honestly reports `found: false`.

---

## Cluster, indexes, shards

```
GET /_cat/health?v
GET /_cat/indices?v&s=index
GET /_cat/shards?v&s=index,shard
GET /_cat/nodes?v&h=name,heap.percent,ram.percent,cpu,disk.used_percent
GET /_cluster/health/documents?pretty
```

Which shard a tenant lives on, and how many would be searched without routing:

```
GET /documents/_search_shards?routing=acme
GET /documents/_search_shards
```

---

## Mapping and settings

```
GET /documents/_mapping
GET /documents/_settings
GET /documents                      # both at once
```

Just the field types:

```
GET /documents/_mapping/field/tenant,title,body,metadata
```

---

## Get a document by id

```
GET /documents/_doc/acme:<doc_id>?routing=acme
```

Only the source, no metadata:

```
GET /documents/_source/acme:<doc_id>?routing=acme
```

Does it exist:

```
HEAD /documents/_doc/acme:<doc_id>?routing=acme
```

If you have the UUID but not the tenant, search instead — this fans out
across shards so it needs no routing:

```
GET /documents/_search
{ "query": { "term": { "doc_id": "<doc_id>" } } }
```

Several at once:

```
GET /documents/_mget
{ "docs": [
    { "_id": "acme:<id1>", "routing": "acme" },
    { "_id": "globex:<id2>", "routing": "globex" } ] }
```

---

## Searching

Everything (small index only):

```
GET /documents/_search
{ "query": { "match_all": {} }, "size": 20 }
```

One tenant, listed:

```
GET /documents/_search?routing=acme
{ "query": { "term": { "tenant": "acme" } },
  "_source": ["tenant", "title", "version"],
  "size": 50 }
```

**What the application actually sends** — tenant filter, boosted title,
fuzziness, highlighting and a facet:

```
GET /documents/_search?routing=acme
{
  "query": {
    "bool": {
      "filter": [ { "term": { "tenant": "acme" } } ],
      "must":   [ { "multi_match": {
                      "query": "refund",
                      "fields": ["title^3", "body"],
                      "fuzziness": "AUTO" } } ]
    }
  },
  "highlight": { "fields": { "body": { "fragment_size": 140,
                                       "number_of_fragments": 1 } } },
  "aggs": { "dept": { "terms": { "field": "metadata.dept", "size": 10 } } },
  "from": 0, "size": 20,
  "track_total_hits": 10000
}
```

`filter` rather than `must` for the tenant: it does not affect the score and
Elasticsearch caches it as a bitset.

Counts only:

```
GET /documents/_count
{ "query": { "term": { "tenant": "acme" } } }
```

Group by tenant:

```
GET /documents/_search
{ "size": 0,
  "aggs": { "by_tenant": { "terms": { "field": "tenant", "size": 20 } } } }
```

---

## Understanding results

**How is text tokenised** — this is why searching `refunds` finds `refund`:

```
GET /documents/_analyze
{ "analyzer": "english",
  "text": "Customers may request refunds within 30 days" }
```

→ `["custom", "mai", "request", "refund", "within", "30", "dai"]`
Stemmed to roots, so index-time and search-time agree.

**Why did this document score that** :

```
GET /documents/_explain/acme:<doc_id>?routing=acme
{ "query": { "multi_match": { "query": "refund",
                              "fields": ["title^3", "body"] } } }
```

**What terms were actually indexed** for a document:

```
GET /documents/_termvectors/acme:<doc_id>?routing=acme&fields=title,body
```

**Validate a query without running it**:

```
GET /documents/_validate/query?explain=true
{ "query": { "match": { "body": "refund" } } }
```

---

## Operational

```
GET /documents/_stats/docs,store,search
GET /_cat/thread_pool/search?v&h=node_name,name,active,queue,rejected
GET /_nodes/stats/indices/search?pretty
```

Refresh (the indexer relies on the 1s default; force it in a test):

```
POST /documents/_refresh
```

---

## Destructive — do not run against anything you care about

```
POST /documents/_delete_by_query
{ "query": { "term": { "tenant": "globex" } } }

DELETE /documents
```

The index is derived and rebuildable from Postgres plus S3, so losing it costs
a reindex rather than data. That is the point of Postgres being the source of
truth.
