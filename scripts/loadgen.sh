#!/usr/bin/env bash
# Generates enough traffic to make the dashboards show something real:
# indexes documents, searches (hot + cold queries), deletes, and deliberately
# trips the per-tenant rate limit.
set -u
GW=${GW:-http://localhost:8080}
DOCS=${DOCS:-25}
SEARCHES=${SEARCHES:-120}

login() { curl -s -X POST "$GW/auth/token" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"demo\"}" \
  | python3 -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))"; }

ACME=$(login alice@acme.com)
GLOBEX=$(login bob@globex.com)
[ -z "$ACME" ] && { echo "login failed"; exit 1; }

WORDS=(refund shipping warranty invoice contract onboarding compliance renewal)
echo "indexing $DOCS documents..."
for i in $(seq 1 "$DOCS"); do
  w=${WORDS[$((RANDOM % ${#WORDS[@]}))]}
  t=$([ $((i % 5)) -eq 0 ] && echo "$GLOBEX" || echo "$ACME")
  curl -s -o /dev/null -X POST "$GW/documents" -H "Authorization: Bearer $t" \
    -H 'Content-Type: application/json' \
    -d "{\"title\":\"Policy $i — $w\",\"body\":\"This document covers $w procedures. Customers may request a $w within 30 days. See section $i for details.\",\"metadata\":{\"dept\":\"ops\",\"year\":2025}}"
done

echo "waiting for the pipeline to drain..."; sleep 4

echo "running $SEARCHES searches (mixed hot/cold, so the cache ratio is real)..."
for i in $(seq 1 "$SEARCHES"); do
  if [ $((i % 3)) -eq 0 ]; then q=${WORDS[$((RANDOM % ${#WORDS[@]}))]}   # cold
  else q=refund; fi                                                       # hot
  curl -s -o /dev/null "$GW/search?q=$q&facets=dept" -H "Authorization: Bearer $ACME"
done

echo "404s and cross-tenant reads..."
for i in 1 2 3; do
  curl -s -o /dev/null "$GW/documents/00000000-0000-0000-0000-00000000000$i" \
    -H "Authorization: Bearer $ACME"
done

echo "tripping the rate limit (globex is capped at 300/min)..."
CODES=$(for i in $(seq 1 400); do
  curl -s -o /dev/null -w '%{http_code} ' "$GW/search?q=refund" -H "Authorization: Bearer $GLOBEX"
done)
echo "  200s: $(echo "$CODES" | tr ' ' '\n' | grep -c 200)   429s: $(echo "$CODES" | tr ' ' '\n' | grep -c 429)"

echo
echo "done. now look at:"
echo "  Grafana     http://localhost:3000/d/deeprunner/deeprunner"
echo "  Prometheus  http://localhost:9090/graph"
