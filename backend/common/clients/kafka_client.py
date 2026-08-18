"""Kafka producer/consumer.

Partition key is doc_id, not tenant. Kafka orders within a partition, and
the only ordering that matters is per document: an UPSERT must not overtake
the DELETE that follows it. Keying on tenant would drop a whale tenant's
whole corpus into one partition.
"""
import json

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from ..config import settings

_producer: KafkaProducer | None = None


def producer() -> KafkaProducer:
    global _producer
    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=settings().kafka_bootstrap.split(","),
            value_serializer=lambda v: json.dumps(v).encode(),
            key_serializer=lambda k: k.encode() if k else None,
            acks="all",              # do not consider it sent until replicated
            retries=3,
            linger_ms=50,
        )
    return _producer


def publish(topic: str, key: str, value: dict) -> None:
    producer().send(topic, key=key, value=value).get(timeout=10)


def consumer(topic: str, group: str) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=settings().kafka_bootstrap.split(","),
        group_id=group,
        value_deserializer=lambda v: json.loads(v.decode()),
        enable_auto_commit=False,       # commit only after the work succeeds
        auto_offset_reset="earliest",
        max_poll_records=100,
        max_poll_interval_ms=900_000,   # slow extraction must not look like death
    )


def ping() -> bool:
    try:
        producer().partitions_for(settings().kafka_topic)
        return True
    except KafkaError:
        return False
    except Exception:
        return False
