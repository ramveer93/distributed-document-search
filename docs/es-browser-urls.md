# Elasticsearch in the browser

No Kibana, no extension. Paste into the address bar.

GET requests cannot carry a body, so anything that needs one goes in a
`source=` parameter instead. Both forms are shown below.

Examples use a real document: `acme:e48b2c7e-1224-47f8-9063-5842d2860b47`

---

## Cluster, indexes, shards

**Every index — size, doc count, health**
```
http://localhost:9200/_cat/indices?v&s=index
```

**Every shard — which holds how many**
```
http://localhost:9200/_cat/shards?v&s=index,shard
```

**Cluster health**
```
http://localhost:9200/_cat/health?v
```

**Nodes — heap, cpu, disk**
```
http://localhost:9200/_cat/nodes?v&h=name,heap.percent,ram.percent,cpu,disk.used_percent
```

**Index stats**
```
http://localhost:9200/documents/_stats/docs,store,search?pretty
```

## Mapping and settings

**Field mapping**
```
http://localhost:9200/documents/_mapping?pretty
```

**Index settings — shard count, analyzer**
```
http://localhost:9200/documents/_settings?pretty
```

## Routing — why one shard answers instead of all

**Which shard `acme` lives on**
```
http://localhost:9200/documents/_search_shards?routing=acme&pretty
```

**Which shards WITHOUT routing**
```
http://localhost:9200/documents/_search_shards?pretty
```

## Get a document by id

**Correct — with routing**
```
http://localhost:9200/documents/_doc/acme:e48b2c7e-1224-47f8-9063-5842d2860b47?routing=acme&pretty
```

**Without routing — returns `found: false` on purpose**

Not missing: the shard is chosen from the id rather than the routing value,
so Elasticsearch looks in the wrong place.
```
http://localhost:9200/documents/_doc/acme:e48b2c7e-1224-47f8-9063-5842d2860b47?pretty
```

**Source only, no metadata**
```
http://localhost:9200/documents/_source/acme:e48b2c7e-1224-47f8-9063-5842d2860b47?routing=acme&pretty
```

**Find it by UUID alone** — fans out, so no routing needed
```
http://localhost:9200/documents/_search?q=doc_id:e48b2c7e-1224-47f8-9063-5842d2860b47&pretty
```

## Simple searches — `q=` needs no body

**Full-text search**
```
http://localhost:9200/documents/_search?q=body:refund&size=5&pretty
```

**One tenant only**
```
http://localhost:9200/documents/_search?q=tenant:acme&size=5&pretty
```

**Count for a tenant**
```
http://localhost:9200/documents/_count?q=tenant:acme&pretty
```

**Everything, newest first**
```
http://localhost:9200/documents/_search?size=20&sort=created_at:desc&_source=tenant,title&pretty
```

**Two terms**
```
http://localhost:9200/documents/_search?q=body:(refund AND warranty)&size=5&pretty
```

## Needs a body, so use `source=`

**How text is tokenised**

Every word stemmed to its root — this is why searching `refunds` finds `refund`.

```
http://localhost:9200/documents/_analyze?source=%7B%22analyzer%22%3A%22english%22%2C%22text%22%3A%22Customers%20may%20request%20refunds%20within%2030%20days%22%7D&source_content_type=application/json&pretty
```

**The exact query the application sends**

Tenant filter, boosted title, fuzziness, highlighting.

```
http://localhost:9200/documents/_search?source=%7B%22query%22%3A%7B%22bool%22%3A%7B%22filter%22%3A%5B%7B%22term%22%3A%7B%22tenant%22%3A%22acme%22%7D%7D%5D%2C%22must%22%3A%5B%7B%22multi_match%22%3A%7B%22query%22%3A%22refund%22%2C%22fields%22%3A%5B%22title%5E3%22%2C%22body%22%5D%2C%22fuzziness%22%3A%22AUTO%22%7D%7D%5D%7D%7D%2C%22highlight%22%3A%7B%22fields%22%3A%7B%22body%22%3A%7B%7D%7D%7D%2C%22size%22%3A5%7D&source_content_type=application/json&pretty&routing=acme
```

**Documents grouped by tenant**

```
http://localhost:9200/documents/_search?source=%7B%22size%22%3A0%2C%22aggs%22%3A%7B%22by_tenant%22%3A%7B%22terms%22%3A%7B%22field%22%3A%22tenant%22%2C%22size%22%3A20%7D%7D%7D%7D&source_content_type=application/json&pretty
```

**Facet by department**

```
http://localhost:9200/documents/_search?source=%7B%22size%22%3A0%2C%22aggs%22%3A%7B%22dept%22%3A%7B%22terms%22%3A%7B%22field%22%3A%22metadata.dept%22%2C%22size%22%3A10%7D%7D%7D%7D&source_content_type=application/json&pretty
```

**Why this document scored what it did**

```
http://localhost:9200/documents/_explain/acme:e48b2c7e-1224-47f8-9063-5842d2860b47?source=%7B%22query%22%3A%7B%22multi_match%22%3A%7B%22query%22%3A%22refund%22%2C%22fields%22%3A%5B%22title%5E3%22%2C%22body%22%5D%7D%7D%7D&source_content_type=application/json&pretty&routing=acme
```

---

## Making your own

Anything with a body follows one pattern:

```
http://localhost:9200/<path>?source=<URL-ENCODED JSON>&source_content_type=application/json&pretty
```

To encode a body:

```bash
python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))" \
  '{"query":{"match_all":{}}}'
```

Or skip the encoding entirely and use curl, where the body is just `-d`:

```bash
curl -s 'localhost:9200/documents/_search?pretty' \
  -H 'Content-Type: application/json' \
  -d '{"query":{"match_all":{}},"size":2}'
```

