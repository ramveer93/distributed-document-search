"""Forward the request downstream.

The original Authorization header goes through untouched, and every service
verifies it again. The gateway owns cross-cutting concerns; it does not own
the trust boundary, so a second ingress path cannot hand a service a forged
identity.
"""
import requests
from flask import Response, g, request

from common.constants import (H_AUTH, H_REQUEST, H_SESSION, H_TENANT, H_USER)
from common.context import context
from common.exceptions import DependencyDown, NotFound

from ..managers import routing

_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "upgrade",
               "proxy-authenticate", "proxy-authorization", "te", "trailer"}

TIMEOUT = (3, 30)   # connect, read


def forward(_any: str = ""):
    target = routing.resolve(request.method, request.path)
    if target is None:
        raise NotFound("no such route")

    ctx = context()
    headers = {
        H_AUTH: request.headers.get(H_AUTH, ""),   # verified again downstream
        H_REQUEST: ctx.request_id,
        H_TENANT: ctx.tenant or "",
        H_USER: ctx.user_id or "",
        H_SESSION: ctx.session_id or "",
        "Content-Type": request.headers.get("Content-Type", "application/json"),
    }
    # an allowlist, so nothing a caller invents reaches a service by accident —
    # but these change the RESPONSE and must survive the hop. Accept in
    # particular: /documents/{id}/raw content-negotiates on it.
    for name in ("Accept", "Accept-Language", "Range", "If-None-Match"):
        value = request.headers.get(name)
        if value:
            headers[name] = value

    try:
        upstream = requests.request(
            method=request.method,
            url=f"{target}{request.full_path.rstrip('?')}",
            headers=headers,
            data=request.get_data(),
            timeout=TIMEOUT,
            allow_redirects=False,      # a 302 to a presigned URL is the answer
        )
    except requests.Timeout:
        raise DependencyDown("upstream timed out")
    except requests.RequestException:
        raise DependencyDown("upstream unreachable")

    passthrough = {k: v for k, v in upstream.headers.items()
                   if k.lower() not in _HOP_BY_HOP}
    passthrough[H_REQUEST] = ctx.request_id
    if g.get("rate_remaining") is not None:
        passthrough["X-RateLimit-Limit"] = str(ctx.rate_limit_rpm)
        passthrough["X-RateLimit-Remaining"] = str(g.rate_remaining)

    return Response(upstream.content, status=upstream.status_code,
                    headers=passthrough)
