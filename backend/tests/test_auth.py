"""Token minting and verification.

The tenant claim is the root of every isolation decision downstream, so the
tests that matter are the ones that try to forge it.
"""
import time

import bcrypt
import jwt
import pytest
from cryptography.hazmat.primitives import serialization

from common import auth
from common.exceptions import Forbidden, Unauthorized
from gateway.managers import auth as gateway_auth
from gateway.managers import keys


@pytest.fixture(scope="module")
def keypair():
    keys._generate()
    return keys.private_key(), keys.public_key()


@pytest.fixture
def verify_locally(monkeypatch, keypair):
    """verify() normally fetches JWKS over HTTP. Swap in the public key we
    already hold — the code path under test is the validation, not the fetch."""
    _, public = keypair

    class _Key:
        key = public

    class _Client:
        def get_signing_key_from_jwt(self, _token):
            return _Key()

    monkeypatch.setattr(auth, "_jwk_client", _Client())
    return lambda token: auth.verify(token, "http://jwks.test/keys")


def _b64(raw: bytes) -> bytes:
    import base64
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _hs256(header: dict, payload: dict, secret: bytes) -> str:
    """Hand-rolled HS256, bypassing PyJWT's refusal to use a PEM as a secret."""
    import hashlib
    import hmac
    import json
    signing_input = (_b64(json.dumps(header).encode()) + b"."
                     + _b64(json.dumps(payload).encode()))
    mac = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return (signing_input + b"." + _b64(mac)).decode()


def mint(private, **overrides):
    now = int(time.time())
    payload = {"iss": "deeprunner", "aud": "deeprunner-api", "sub": "u-1",
               "tenant": "acme", "sid": "s-1", "iat": now, "exp": now + 900}
    payload.update(overrides)
    alg = payload.pop("__alg", "RS256")
    secret = payload.pop("__secret", private)
    return jwt.encode(payload, secret, algorithm=alg)


# ------------------------------------------------------------ header parsing

class TestBearerToken:
    def test_extracts_the_token(self):
        assert auth.bearer_token({"Authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"

    @pytest.mark.parametrize("header", [
        {},                                     # absent
        {"Authorization": ""},                  # empty
        {"Authorization": "abc.def.ghi"},       # no scheme
        {"Authorization": "Basic dXNlcjpwdw=="},
        {"Authorization": "bearer abc"},        # case matters, per RFC 6750 examples
    ])
    def test_rejects_anything_else(self, header):
        with pytest.raises(Unauthorized):
            auth.bearer_token(header)


# -------------------------------------------------------------- verification

class TestVerify:
    def test_a_valid_token_round_trips(self, keypair, verify_locally):
        private, _ = keypair
        claims = verify_locally(mint(private))
        assert claims["tenant"] == "acme"
        assert claims["sub"] == "u-1"

    def test_an_hs256_token_is_rejected(self, keypair, verify_locally):
        """The algorithm-confusion attack: sign HS256 using the *public* key
        as the shared secret. A verifier that reads the algorithm out of the
        token header would validate it, because the public key is public.

        Signed with raw HMAC rather than through PyJWT — PyJWT refuses to
        encode a PEM as an HMAC secret, and an attacker is under no such
        obligation. The defence being tested is on the verify side.
        """
        _, public = keypair
        public_pem = public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo)
        forged = _hs256(
            {"alg": "HS256", "typ": "JWT"},
            {"iss": "deeprunner", "aud": "deeprunner-api", "sub": "attacker",
             "tenant": "victim-tenant", "exp": int(time.time()) + 900},
            secret=public_pem)

        with pytest.raises(Unauthorized):
            verify_locally(forged)

    def test_an_unsigned_token_is_rejected(self, verify_locally):
        forged = jwt.encode({"tenant": "victim", "sub": "attacker"},
                            key="", algorithm="none")
        with pytest.raises(Unauthorized):
            verify_locally(forged)

    def test_an_expired_token_is_rejected(self, keypair, verify_locally):
        private, _ = keypair
        with pytest.raises(Unauthorized, match="expired"):
            verify_locally(mint(private, exp=int(time.time()) - 1))

    def test_a_token_for_another_audience_is_rejected(self, keypair, verify_locally):
        """Stops a token minted for a sibling system being replayed here."""
        private, _ = keypair
        with pytest.raises(Unauthorized):
            verify_locally(mint(private, aud="some-other-api"))

    def test_a_token_from_another_issuer_is_rejected(self, keypair, verify_locally):
        private, _ = keypair
        with pytest.raises(Unauthorized):
            verify_locally(mint(private, iss="evil"))

    def test_a_tampered_payload_is_rejected(self, keypair, verify_locally):
        """The whole point of signing: edit the tenant claim and the
        signature no longer matches."""
        private, _ = keypair
        header, payload, signature = mint(private).split(".")
        import base64
        import json
        raw = json.loads(base64.urlsafe_b64decode(payload + "=="))
        raw["tenant"] = "victim-tenant"
        swapped = base64.urlsafe_b64encode(
            json.dumps(raw).encode()).rstrip(b"=").decode()
        with pytest.raises(Unauthorized):
            verify_locally(f"{header}.{swapped}.{signature}")

    def test_garbage_is_rejected_as_unauthorized_not_a_crash(self, verify_locally):
        with pytest.raises(Unauthorized):
            verify_locally("not-a-jwt")


# ----------------------------------------------------------------- identity

class TestClaimsToIdentity:
    def test_extracts_tenant_user_and_session(self):
        assert auth.claims_to_identity(
            {"tenant": "acme", "sub": "u-1", "sid": "s-9"}) == ("acme", "u-1", "s-9")

    def test_session_is_optional(self):
        assert auth.claims_to_identity({"tenant": "acme", "sub": "u-1"})[2] is None

    @pytest.mark.parametrize("claims", [
        {"sub": "u-1"},                       # no tenant
        {"tenant": "acme"},                   # no subject
        {"tenant": "", "sub": "u-1"},         # empty tenant
        {},
    ])
    def test_an_incomplete_token_is_not_an_identity(self, claims):
        """A structurally valid, correctly signed token can still be missing
        the claim everything downstream depends on."""
        with pytest.raises(Unauthorized):
            auth.claims_to_identity(claims)


# ------------------------------------------------------------------- keys

class TestKeys:
    def test_the_derived_public_key_is_cached(self, keypair):
        """Not a micro-optimisation: re-deriving this per request measured
        34.5 ms, more than Postgres, Redis and Elasticsearch combined."""
        assert keys.public_key() is keys.public_key()

    def test_jwks_publishes_only_the_public_half(self, keypair):
        jwk = keys.jwks()["keys"][0]
        assert jwk["kty"] == "RSA"
        assert jwk["alg"] == "RS256" and jwk["use"] == "sig"
        assert set(jwk) & {"d", "p", "q", "dp", "dq", "qi"} == set(), \
            "private key material must never appear in JWKS"

    def test_tokens_carry_the_kid_that_jwks_advertises(self, keypair):
        private, _ = keypair
        token = jwt.encode({"sub": "u"}, private, algorithm="RS256",
                           headers={"kid": keys.kid()})
        assert jwt.get_unverified_header(token)["kid"] == keys.jwks()["keys"][0]["kid"]


# ------------------------------------------------------------------- login

class TestLogin:
    @pytest.fixture
    def user_row(self):
        return {"user_id": "u-1", "namespace": "acme", "status": "ACTIVE",
                "password_hash": bcrypt.hashpw(b"correct-horse",
                                               bcrypt.gensalt()).decode()}

    def test_valid_credentials_mint_a_token_carrying_the_db_tenant(
            self, monkeypatch, keypair, verify_locally, user_row):
        """The tenant claim comes from the row, never from anything the
        caller sent — that is the property the whole isolation model rests on."""
        monkeypatch.setattr(gateway_auth.users, "find_login", lambda _e: user_row)
        token, ttl, namespace = gateway_auth.login("a@acme.test", "correct-horse")
        assert namespace == "acme" and ttl > 0
        assert verify_locally(token)["tenant"] == "acme"

    def test_an_unknown_user_and_a_wrong_password_are_indistinguishable(
            self, monkeypatch, user_row):
        """Different errors here tell an attacker which addresses are
        registered."""
        monkeypatch.setattr(gateway_auth.users, "find_login", lambda _e: None)
        with pytest.raises(Unauthorized) as missing:
            gateway_auth.login("nobody@acme.test", "whatever")

        monkeypatch.setattr(gateway_auth.users, "find_login", lambda _e: user_row)
        with pytest.raises(Unauthorized) as wrong:
            gateway_auth.login("a@acme.test", "wrong-password")

        assert str(missing.value) == str(wrong.value)

    def test_a_suspended_tenant_cannot_log_in(self, monkeypatch, user_row):
        monkeypatch.setattr(gateway_auth.users, "find_login",
                            lambda _e: {**user_row, "status": "SUSPENDED"})
        with pytest.raises(Forbidden, match="suspended"):
            gateway_auth.login("a@acme.test", "correct-horse")

    def test_each_login_gets_a_distinct_session_and_token_id(
            self, monkeypatch, keypair, user_row):
        """jti is what a revocation denylist keys on, so two logins sharing
        one would revoke both."""
        monkeypatch.setattr(gateway_auth.users, "find_login", lambda _e: user_row)
        a = jwt.decode(gateway_auth.login("a@acme.test", "correct-horse")[0],
                       options={"verify_signature": False})
        b = jwt.decode(gateway_auth.login("a@acme.test", "correct-horse")[0],
                       options={"verify_signature": False})
        assert a["jti"] != b["jti"]
        assert a["sid"] != b["sid"]

    def test_the_token_expires(self, monkeypatch, keypair, user_row):
        monkeypatch.setattr(gateway_auth.users, "find_login", lambda _e: user_row)
        claims = jwt.decode(gateway_auth.login("a@acme.test", "correct-horse")[0],
                            options={"verify_signature": False})
        assert 0 < claims["exp"] - claims["iat"] <= 3600, \
            "a long-lived token cannot be un-issued"
