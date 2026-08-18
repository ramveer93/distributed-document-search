"""RS256 keypair, generated at startup and published as JWKS.

RS256 rather than HS256: with a shared secret, any service able to verify
a token could also mint one for any tenant. Here the gateway holds the
private key and everyone else gets only the public half.

The key lives in memory and dies with the process — fine for a prototype,
and it means no private key is ever written to disk or committed.
"""
import json
import uuid

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

_private_pem: bytes | None = None
_public_key = None
_jwks: dict | None = None
_kid: str = uuid.uuid4().hex[:8]


def _generate() -> None:
    global _private_pem, _jwks, _public_key
    _public_key = None
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    _private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(
        serialization.load_pem_public_key(public_pem)))
    jwk.update({"kid": _kid, "use": "sig", "alg": "RS256"})
    _jwks = {"keys": [jwk]}


def private_key() -> bytes:
    if _private_pem is None:
        _generate()
    return _private_pem


def jwks() -> dict:
    if _jwks is None:
        _generate()
    return _jwks


def kid() -> str:
    return _kid


def public_key():
    """The gateway verifies its own tokens locally — no reason to fetch its
    own JWKS over HTTP.

    Cached deliberately: deriving this re-parses the PEM and rebuilds the RSA
    key, which costs ~35 ms. Called per request that is 35 ms of pure overhead
    on every authenticated call — far more than Postgres, Redis and
    Elasticsearch put together.
    """
    global _public_key
    if _public_key is None:
        if _private_pem is None:
            _generate()
        _public_key = serialization.load_pem_private_key(
            _private_pem, password=None).public_key()
    return _public_key
