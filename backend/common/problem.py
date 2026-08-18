"""RFC 7807 responses. One place, so every service errors identically and
every error carries the trace id."""
from flask import g, jsonify, request

from .constants import H_REQUEST
from .exceptions import AppError


def problem(exc: AppError):
    body = {
        "type": exc.type,
        "title": exc.title,
        "status": exc.status,
        "trace_id": g.get("request_id") or request.headers.get(H_REQUEST),
    }
    if exc.detail:
        body["detail"] = exc.detail

    response = jsonify(body)
    response.status_code = exc.status
    response.mimetype = "application/problem+json"

    if exc.status == 429:
        response.headers["Retry-After"] = str(getattr(exc, "retry_after", 60))
        response.headers["X-RateLimit-Limit"] = str(getattr(exc, "limit", 0))
        response.headers["X-RateLimit-Remaining"] = "0"
    return response


def register_error_handlers(app, logger):
    @app.errorhandler(AppError)
    def _app_error(exc: AppError):
        logger.warning(exc.title, extra={
            "request_id": g.get("request_id"),
            "status": exc.status,
        })
        return problem(exc)

    @app.errorhandler(404)
    def _404(_):
        from .exceptions import NotFound
        return problem(NotFound("no such route"))

    @app.errorhandler(Exception)
    def _unhandled(exc: Exception):
        logger.exception("unhandled", extra={
            "request_id": g.get("request_id"),
        })
        return problem(AppError("unexpected error"))
