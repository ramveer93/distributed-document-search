from flask import Blueprint

from ..handlers import documents as h

bp = Blueprint("index", __name__)
bp.add_url_rule("/documents", view_func=h.create, methods=["POST"])
