#!/usr/bin/env bash
# Latency of /search, split by cache state. Reports the percentiles that
# matter rather than an average, which hides exactly the tail we care about.
set -u
B="${BASE:-http://localhost:3001/api}"
N="${N:-60}"
T=$(curl -s -X POST "$B/auth/token" -H 'Content-Type: application/json' \
   --data-binary '{"email":"alice@acme.com","password":"demo"}' \
   | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

measure() { # $1 label, $2 = "hot" | "cold"
  local times=()
  for i in $(seq 1 "$N"); do
    local q="warranty"
    [ "$2" = "cold" ] && q="term$RANDOM$i"       # unique -> always a miss
    local ms
    ms=$(curl -s -o /dev/null -w '%{time_total}' "$B/search?q=$q" \
         -H "Authorization: Bearer $T")
    times+=("$(python3 -c "print(f'{$ms*1000:.1f}')")")
  done
  printf '%s\n' "${times[@]}" | python3 -c "
import sys
v=sorted(float(x) for x in sys.stdin)
p=lambda q: v[min(len(v)-1,int(len(v)*q))]
print(f'  $1  n=${#times[@]}  p50={p(.5):6.1f}ms  p95={p(.95):6.1f}ms  max={v[-1]:6.1f}ms')"
}

curl -s -o /dev/null "$B/search?q=warranty" -H "Authorization: Bearer $T"   # warm it
measure "cache HIT " hot
measure "cache MISS" cold
