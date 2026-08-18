from flask import Blueprint

from ..handlers import auth as h

bp = Blueprint("auth", __name__)
bp.add_url_rule("/auth/token", view_func=h.login, methods=["POST"])
bp.add_url_rule("/.well-known/jwks.json", view_func=h.jwks, methods=["GET"])
