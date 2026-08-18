#!/usr/bin/env bash
# Read-only view of what Elasticsearch actually holds. Safe to run on camera.
ES="${ES:-http://localhost:9200}"

echo "── indexes ──────────────────────────────────────────────────────────"
curl -s "$ES/_cat/indices?v&s=index&h=health,status,index,pri,rep,docs.count,docs.deleted,store.size"

echo; echo "── shards ───────────────────────────────────────────────────────────"
curl -s "$ES/_cat/shards?v&s=index,shard&h=index,shard,prirep,state,docs,store,node"

echo; echo "── cluster ──────────────────────────────────────────────────────────"
curl -s "$ES/_cat/health?v&h=status,node.total,shards,pri,unassign,active_shards_percent"

echo; echo "── documents by tenant ──────────────────────────────────────────────"
curl -s "$ES/documents/_search?size=0" -H 'Content-Type: application/json' -d '{
  "aggs":{"tenant":{"terms":{"field":"tenant","size":20}}}}' | python3 -c "
import sys,json
d=json.load(sys.stdin)
print(f\"  total indexed: {d['hits']['total']['value']}\")
for b in d['aggregations']['tenant']['buckets']:
    print(f\"    {b['key']:12} {b['doc_count']:>4}\")"

echo; echo "── documents (id, tenant, title) ────────────────────────────────────"
curl -s "$ES/documents/_search?size=${LIMIT:-15}&sort=created_at:desc" \
  -H 'Content-Type: application/json' \
  -d '{"_source":["tenant","title","version"]}' | python3 -c "
import sys,json
hits=json.load(sys.stdin)['hits']['hits']
print(f\"  {'shard-routed id':<46} {'tenant':<9} title\")
print('  ' + '-'*46 + ' ' + '-'*9 + ' ' + '-'*34)
for h in hits:
    s=h['_source']
    print(f\"  {h['_id']:<46} {s['tenant']:<9} {s['title'][:34]}\")"
