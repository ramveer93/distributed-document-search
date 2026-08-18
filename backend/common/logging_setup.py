"""Structured JSON logs with the request id on every line.

The gateway mints the id and forwards it; services log it; the indexer reads
it off the Kafka message. So one grep follows a document from HTTP request
through to indexed, across three processes and a queue.
"""
import json
import logging
import sys
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": getattr(record, "service", "-"),
            "msg": record.getMessage(),
        }
        for key in ("request_id", "tenant", "user_id", "doc_id",
                    "status", "took_ms", "attempt"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload)


class _ContextAdapter(logging.LoggerAdapter):
    """LoggerAdapter.process() REPLACES kwargs["extra"] by default, which
    silently drops every per-call field. Merge instead, so request_id and
    doc_id actually reach the formatter."""

    def process(self, msg, kwargs):
        merged = dict(self.extra or {})
        merged.update(kwargs.get("extra") or {})
        kwargs["extra"] = merged
        return msg, kwargs


def configure(service: str, level: str = "INFO") -> logging.LoggerAdapter:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    logging.getLogger("elastic_transport").setLevel("WARNING")
    logging.getLogger("kafka").setLevel("WARNING")
    logging.getLogger("botocore").setLevel("WARNING")

    return _ContextAdapter(logging.getLogger(service), {"service": service})
