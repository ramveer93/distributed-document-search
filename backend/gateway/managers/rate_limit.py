"""Per-tenant rate limiting at the edge, so an over-quota tenant is rejected
before burning any API capacity.

Fixed window: one INCR plus one EXPIRE. A sliding window is more accurate but
needs a sorted set per tenant, which is not worth it for fairness limiting.
"""
import time

from common.clients import redis_client
from common.constants import key_rate_limit
from common.exceptions import RateLimited


def check(tenant: str, limit_rpm: int) -> int:
    """Returns remaining allowance, raises RateLimited when exhausted."""
    minute = time.strftime("%Y%m%d%H%M")
    key = key_rate_limit(tenant, minute)

    pipe = redis_client.client().pipeline()
    pipe.incr(key)
    pipe.expire(key, 60)
    count, _ = pipe.execute()

    if count > limit_rpm:
        raise RateLimited(
            detail=f"{limit_rpm} requests/min exceeded",
            retry_after=60 - int(time.strftime("%S")),
            limit=limit_rpm,
        )
    return max(0, limit_rpm - count)
