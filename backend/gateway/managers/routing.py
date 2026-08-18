"""Which service owns which route.

POST /documents goes to index-service while GET/DELETE /documents/{id} go to
document-service — same prefix, different owners — so routing is by method
and shape, not prefix alone.
"""
import re

from common.config import settings

_RULES = [
    # (methods, compiled path, service url)
    ({"GET"},            re.compile(r"^/search/?$"),                    "search"),
    ({"POST"},           re.compile(r"^/documents/?$"),                 "index"),
    ({"GET"},            re.compile(r"^/documents/?$"),                 "document"),
    ({"GET", "DELETE"},  re.compile(r"^/documents/[^/]+/?$"),           "document"),
    ({"GET"},            re.compile(r"^/documents/[^/]+/raw/?$"),       "document"),
]


def resolve(method: str, path: str) -> str | None:
    for methods, pattern, _owner in _RULES:
        if method in methods and pattern.match(path):
            # one API deployable hosts all three blueprints; _owner records
            # which package owns the route, for when they are split out
            return settings().api_url
    return None
