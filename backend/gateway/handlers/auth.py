"""Handlers marshal HTTP <-> managers. No business logic lives here."""
from flask import jsonify, request
from pydantic import ValidationError

from common.exceptions import ValidationFailed

from ..managers import auth as auth_manager
from ..managers import keys
from ..schemas import LoginIn, TokenOut


def login():
    try:
        payload = LoginIn.model_validate(request.get_json(silent=True) or {})
    except ValidationError as exc:
        raise ValidationFailed(str(exc.errors()[0]["msg"]))

    token, ttl, tenant = auth_manager.login(payload.email, payload.password)
    return jsonify(TokenOut(access_token=token, expires_in=ttl,
                            tenant=tenant).model_dump()), 200


def jwks():
    """Public half of the signing key. Services fetch and cache this, which
    is what lets them verify tokens without holding a shared secret."""
    return jsonify(keys.jwks()), 200
