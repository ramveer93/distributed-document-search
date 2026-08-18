"""JWT verification, shared by every service.

The gateway mints tokens and forwards the original Authorization header.
Each service verifies it INDEPENDENTLY rather than trusting an injected
identity header — so a second ingress path or a misconfigured forward
cannot hand a service a forged tenant.
"""
import time

import jwt
import requests
from jwt import PyJWKClient

from .config import settings
from .exceptions import Unauthorized

_jwk_client: PyJWKClient | None = None
_public_key_cache: tuple[float, str] | None = None


def bearer_token(headers) -> str:
    raw = headers.get("Authorization", "")
    if not raw.startswith("Bearer "):
        raise Unauthorized("missing bearer token")
    return raw[7:]


def verify(token: str, jwks_url: str) -> dict:
    """Verify RS256 against the gateway's published public key.

    RS256, not HS256: with a shared secret every service that can verify
    a token can also mint one for any tenant.
    """
    global _jwk_client
    try:
        if _jwk_client is None:
            _jwk_client = PyJWKClient(jwks_url, cache_keys=True, lifespan=300)
        signing_key = _jwk_client.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=settings().jwt_audience,
            issuer=settings().jwt_issuer,
        )
    except jwt.ExpiredSignatureError:
        raise Unauthorized("token expired")
    except jwt.InvalidTokenError as exc:
        raise Unauthorized(f"invalid token: {exc}")
    except requests.RequestException:
        raise Unauthorized("cannot reach the key endpoint")


def claims_to_identity(claims: dict) -> tuple[str, str, str | None]:
    """A token says WHO. It cannot say what is currently allowed —
    suspension, plan and rate limits still come from the database."""
    tenant = claims.get("tenant")
    user_id = claims.get("sub")
    if not tenant or not user_id:
        raise Unauthorized("token is missing tenant or sub")
    return tenant, user_id, claims.get("sid")
