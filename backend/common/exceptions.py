"""Domain errors. Each maps to one RFC 7807 response in problem.py, so
handlers never build status codes by hand."""


class AppError(Exception):
    status = 500
    type = "/errors/internal"
    title = "Internal error"

    def __init__(self, detail: str | None = None):
        super().__init__(detail or self.title)
        self.detail = detail


class ValidationFailed(AppError):
    status, type, title = 422, "/errors/validation", "Validation failed"


class Unauthorized(AppError):
    status, type, title = 401, "/errors/unauthorized", "Unauthorized"


class Forbidden(AppError):
    status, type, title = 403, "/errors/forbidden", "Forbidden"


class NotFound(AppError):
    """Also returned when a document belongs to another tenant.

    Deliberately not 403 — a 403 would confirm the document exists, which
    turns id enumeration into an information leak.
    """
    status, type, title = 404, "/errors/not-found", "Not found"


class RateLimited(AppError):
    status, type, title = 429, "/errors/rate-limited", "Rate limit exceeded"

    def __init__(self, detail=None, retry_after: int = 60, limit: int = 0):
        super().__init__(detail)
        self.retry_after = retry_after
        self.limit = limit


class PayloadTooLarge(AppError):
    status, type, title = 413, "/errors/too-large", "Payload too large"


class DependencyDown(AppError):
    status, type, title = 503, "/errors/dependency", "Dependency unavailable"
