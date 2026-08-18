"""Health, in two flavours.

/health         public, no auth — k8s probes cannot present a token
/health/detail  dependency status; reveals the stack, so keep it internal
"""
from flask import Blueprint, jsonify


def blueprint(service: str, checks: dict) -> Blueprint:
    bp = Blueprint("health", __name__)

    @bp.get("/health")
    def health():
        return jsonify({"status": "ok", "service": service})

    @bp.get("/health/detail")
    def detail():
        deps = {name: ("up" if fn() else "down") for name, fn in checks.items()}
        ok = all(v == "up" for v in deps.values())
        return jsonify({
            "status": "ok" if ok else "degraded",
            "service": service,
            "deps": deps,
        }), (200 if ok else 503)

    return bp
