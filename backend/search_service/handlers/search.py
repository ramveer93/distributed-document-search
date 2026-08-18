from flask import jsonify
from flask import request as flask_request
from pydantic import ValidationError

from common.context import context
from common.exceptions import ValidationFailed

from ..managers import search as manager
from ..schemas import Hit, SearchOut, SearchQuery, Total


def search():
    args = flask_request.args
    try:
        query = SearchQuery(
            q=args.get("q", ""),
            page=int(args.get("page", 1)),
            size=int(args.get("size", 20)),
            facets=[f for f in args.get("facets", "").split(",") if f],
            fuzzy=args.get("fuzzy", "true").lower() != "false",
            highlight=args.get("highlight", "true").lower() != "false",
        )
    except (ValidationError, ValueError) as exc:
        raise ValidationFailed(str(exc))

    ctx = context()
    result, cache_state = manager.run(
        ctx.tenant, query.q, query.page, query.size,
        query.facets, query.fuzzy, query.highlight)

    out = SearchOut(
        query=query.q, tenant=ctx.tenant,
        total=Total(**result["total"]),
        page=query.page, size=query.size,
        took_ms=result["took_ms"], cache=cache_state,
        hits=[Hit(**h) for h in result["hits"]],
        facets=result["facets"],
    )
    return jsonify(out.model_dump()), 200
