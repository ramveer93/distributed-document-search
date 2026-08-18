from flask import Blueprint

from ..handlers import search as h

bp = Blueprint("search", __name__)
bp.add_url_rule("/search", view_func=h.search, methods=["GET"])
