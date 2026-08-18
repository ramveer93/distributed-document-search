#!/usr/bin/env bash
# End-to-end smoke test against the running stack, through the frontend origin
# (so it exercises the nginx proxy too, exactly as a browser would).
set -u
B="${BASE:-http://localhost:3001/api}"
ok=0; bad=0
pass(){ printf "  \033[32m✓\033[0m %s\n" "$1"; ok=$((ok+1)); }
fail(){ printf "  \033[31m✗\033[0m %s  (expected %s, got %s)\n" "$1" "$2" "$3"; bad=$((bad+1)); }
chk(){ if [ "$2" = "$3" ]; then pass "$1 → $3"; else fail "$1" "$3" "$2"; fi; }
code(){ curl -s -o /dev/null -w '%{http_code}' "$@"; }
jget(){ python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }
login(){ curl -s -X POST "$B/auth/token" -H 'Content-Type: application/json' \
         --data-binary "{\"email\":\"$1\",\"password\":\"$2\"}"; }

echo "── auth ───────────────────────────────────────────────"
chk "no token"            "$(code "$B/search?q=x")" 401
chk "bad password"        "$(login alice@acme.com wrong | jget "d['status']")" 401
chk "suspended tenant"    "$(login carol@initech.com demo | jget "d['status']")" 403
ACME=$(login alice@acme.com demo | jget "d['access_token']")
GLOBEX=$(login bob@globex.com demo | jget "d['access_token']")
[ -n "$ACME" ] && pass "tokens issued for acme + globex"

A=(-H "Authorization: Bearer $ACME"); G=(-H "Authorization: Bearer $GLOBEX")

echo "── search ─────────────────────────────────────────────"
chk "acme finds refunds"  "$(curl -s "${A[@]}" "$B/search?q=refunds" | jget "'yes' if d['total']['value']>0 else 'no'")" yes
chk "facets returned"     "$(curl -s "${A[@]}" "$B/search?q=refunds&facets=dept" | jget "'yes' if d['facets'].get('dept') else 'no'")" yes
chk "highlight markup"    "$(curl -s "${A[@]}" "$B/search?q=refund" | jget "'yes' if any('<em>' in (h.get('snippet') or '') for h in d['hits']) else 'no'")" yes
curl -s "${A[@]}" "$B/search?q=cachetest$RANDOM" >/dev/null
Q="warranty"
C1=$(curl -s "${A[@]}" "$B/search?q=$Q" | jget "d['cache']")
C2=$(curl -s "${A[@]}" "$B/search?q=$Q" | jget "d['cache']")
chk "second search cached" "$C2" HIT
chk "deep page rejected"  "$(code "${A[@]}" "$B/search?q=a&page=99999")" 422

echo "── tenant isolation ───────────────────────────────────"
DOC=$(curl -s -X POST "$B/documents" "${A[@]}" -H 'Content-Type: application/json' \
  --data-binary '{"title":"Acme confidential bands","body":"Secret compensation bands.","metadata":{"dept":"hr"}}' | jget "d['id']")
sleep 3
chk "owner reads it"      "$(code "${A[@]}" "$B/documents/$DOC")" 200
chk "other tenant → 404"  "$(code "${G[@]}" "$B/documents/$DOC")" 404
chk "other tenant search" "$(curl -s "${G[@]}" "$B/search?q=confidential" | jget "d['total']['value']")" 0
chk "other tenant delete" "$(code -X DELETE "${G[@]}" "$B/documents/$DOC")" 404

echo "── large body → S3 ────────────────────────────────────"
python3 -c "import json;print(json.dumps({'title':'Smoke big doc','body':'compliance review '*20000,'metadata':{'dept':'legal'}}))" > /tmp/smoke_big.json
BIG=$(curl -s -X POST "$B/documents" "${A[@]}" -H 'Content-Type: application/json' --data-binary @/tmp/smoke_big.json | jget "d['id']")
sleep 4
chk "body not inlined"    "$(curl -s "${A[@]}" "$B/documents/$BIG" | jget "'link' if d.get('links') and not d.get('body') else 'inline'")" link
chk "presigned redirect"  "$(code "${A[@]}" "$B/documents/$BIG/raw")" 302

echo "── delete ─────────────────────────────────────────────"
chk "delete"              "$(code -X DELETE "${A[@]}" "$B/documents/$DOC")" 204
sleep 3
chk "gone from search"    "$(curl -s "${A[@]}" "$B/search?q=confidential" | jget "d['total']['value']")" 0
chk "gone from GET"       "$(code "${A[@]}" "$B/documents/$DOC")" 404

echo "── rate limit ─────────────────────────────────────────"
N429=$(for i in $(seq 1 340); do code "${G[@]}" "$B/search?q=refund"; echo; done | grep -c 429)
[ "$N429" -gt 0 ] && pass "429s returned after globex's 300/min cap ($N429)" || fail "rate limit" ">0 429s" "0"

echo
printf "  \033[1m%s passed, %s failed\033[0m\n" "$ok" "$bad"
exit $([ "$bad" -eq 0 ] && echo 0 || echo 1)
