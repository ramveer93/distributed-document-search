#!/usr/bin/env bash
# Read-only view of the source of truth. Safe to run on camera.
# Pass a doc id to focus on one document:  ./scripts/pg.sh <doc_id>
psql() { docker compose exec -T postgres psql -U deeprunner -d deeprunner "$@"; }

if [ -n "${1:-}" ]; then
  echo "── document $1 ──"
  psql -c "SELECT tenant, title, status, version, byte_size, content_type,
                  COALESCE(s3_key,'(body is in the row)') AS storage,
                  failure_reason, created_at, updated_at
             FROM documents WHERE doc_id = '$1';" -x
  echo "── its outbox entries (newest first) ──"
  psql -c "SELECT seq, op, version, request_id,
                  published_at IS NOT NULL AS relayed, created_at
             FROM index_outbox WHERE doc_id = '$1' ORDER BY seq DESC;"
  exit 0
fi

echo "── tables ──"
psql -c "\dt"

echo "── tenants ──"
psql -c "SELECT namespace, display_name, status, rate_limit_rpm, index_group
           FROM tenants ORDER BY namespace;"

echo "── documents by tenant and status ──"
psql -c "SELECT tenant, status, count(*),
                pg_size_pretty(sum(byte_size)) AS bytes
           FROM documents GROUP BY tenant, status ORDER BY tenant, status;"

echo "── where the bodies live ──"
psql -c "SELECT CASE WHEN s3_key IS NULL THEN 'inline in the row'
                     ELSE 'S3' END AS storage,
                count(*), pg_size_pretty(sum(byte_size)) AS bytes
           FROM documents GROUP BY 1;"

echo "── outbox: anything stuck? ──"
psql -c "SELECT count(*) FILTER (WHERE published_at IS NULL) AS unpublished,
                count(*) FILTER (WHERE published_at IS NOT NULL) AS relayed,
                count(*) AS total
           FROM index_outbox;"

echo "── 10 most recent documents ──"
psql -c "SELECT left(doc_id::text,8) AS id, tenant, left(title,30) AS title,
                status, byte_size,
                CASE WHEN s3_key IS NULL THEN 'row' ELSE 's3' END AS body
           FROM documents ORDER BY created_at DESC LIMIT 10;"
