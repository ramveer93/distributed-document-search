from flask import Blueprint

from ..handlers import proxy as h

bp = Blueprint("proxy", __name__)
bp.add_url_rule("/<path:_any>", view_func=h.forward,
                methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
