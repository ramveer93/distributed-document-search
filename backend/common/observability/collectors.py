"""Gauges derived from database state.

These are the numbers that reveal SILENT failure: a stuck relay or a growing
FAILED backlog produces no error anyone sees, it just quietly stops indexing.

Implemented as a scrape-time collector with a short cache, so the values are
fresh without querying Postgres on every request.
"""
import time

from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

_CACHE_SECONDS = 10


class PipelineCollector(Collector):
    def __init__(self):
        self._at = 0.0
        self._snapshot: tuple[int, dict[str, int]] = (0, {})

    def _read(self) -> tuple[int, dict[str, int]]:
        if time.monotonic() - self._at < _CACHE_SECONDS:
            return self._snapshot

        from sqlalchemy import func, select

        from ..db import Document, IndexOutbox, session
        try:
            with session() as s:
                depth = s.scalar(
                    select(func.count())
                    .select_from(IndexOutbox)
                    .where(IndexOutbox.published_at.is_(None))
                ) or 0
                by_status = dict(
                    s.execute(
                        select(Document.status, func.count())
                        .group_by(Document.status)
                    ).all()
                )
            self._snapshot = (depth, by_status)
            self._at = time.monotonic()
        except Exception:
            pass          # a scrape must never take the process down
        return self._snapshot

    def collect(self):
        depth, by_status = self._read()

        yield GaugeMetricFamily(
            "outbox_unpublished_depth",
            "Outbox rows not yet published. Growth means the relay is stuck "
            "and documents are being accepted but never indexed.",
            value=depth)

        docs = GaugeMetricFamily(
            "documents_by_status", "Documents by lifecycle status",
            labels=["status"])
        for status in ("PENDING", "LIVE", "FAILED", "DELETED"):
            docs.add_metric([status], by_status.get(status, 0))
        yield docs


def register(registry=None) -> None:
    from prometheus_client import REGISTRY
    (registry or REGISTRY).register(PipelineCollector())
