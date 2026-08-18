from flask import Blueprint

from ..handlers import documents as h

bp = Blueprint("document", __name__)
bp.add_url_rule("/documents", view_func=h.index, methods=["GET"])
bp.add_url_rule("/documents/<doc_id>", view_func=h.get, methods=["GET"])
bp.add_url_rule("/documents/<doc_id>/raw", view_func=h.raw, methods=["GET"])
bp.add_url_rule("/documents/<doc_id>", view_func=h.delete, methods=["DELETE"])
