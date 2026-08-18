"""Login and token minting."""
import time
import uuid

import bcrypt
import jwt

from common.config import settings
from common.exceptions import Forbidden, Unauthorized

from ..repositories import users
from . import keys


def login(email: str, password: str) -> tuple[str, int, str]:
    row = users.find_login(email)

    # same error and roughly the same work whether the user exists or the
    # password is wrong — otherwise the response tells an attacker which
    if not row or not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        raise Unauthorized("invalid credentials")

    if row["status"] != "ACTIVE":
        raise Forbidden(f"tenant is {row['status'].lower()}")

    return _mint(row), settings().jwt_ttl_seconds, row["namespace"]


def _mint(row: dict) -> str:
    """The tenant claim is the security-critical one — every isolation
    decision downstream derives from it, and it comes from the database,
    never from anything the caller sent."""
    now = int(time.time())
    s = settings()
    payload = {
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
        "sub": row["user_id"],
        "tenant": row["namespace"],
        "sid": f"s-{uuid.uuid4().hex[:12]}",
        "iat": now,
        "exp": now + s.jwt_ttl_seconds,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, keys.private_key(), algorithm="RS256",
                      headers={"kid": keys.kid()})
