"""Redis: rate-limit counters, cache versions, query cache, doc cache."""
import redis

from ..config import settings

_client: redis.Redis | None = None


def client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(settings().redis_url, decode_responses=True,
                                 socket_timeout=2, socket_connect_timeout=2)
    return _client


def ping() -> bool:
    try:
        return bool(client().ping())
    except Exception:
        return False
