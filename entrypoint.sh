#!/bin/sh
# Stale files from a previous boot would inflate every counter, so the
# prometheus multiprocess directory starts empty. Then exec whatever the
# compose command asked for.
rm -rf "${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus}"/* 2>/dev/null || true
mkdir -p "${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus}"
exec "$@"
