"""Values that must agree across services. Change here, not in a handler."""

# ------------------------------------------------------------- doc status
STATUS_PENDING = "PENDING"
STATUS_LIVE    = "LIVE"
STATUS_FAILED  = "FAILED"
STATUS_DELETED = "DELETED"

# the indexer acts on PENDING and nothing else. that single rule is what
# makes it safe for the LIVE write-back not to loop, and for DELETED rows
# to be picked up as removals rather than re-indexed.
INDEXABLE_STATUSES = (STATUS_PENDING,)

# ------------------------------------------------------------- outbox ops
OP_UPSERT = "UPSERT"
OP_DELETE = "DELETE"

# ------------------------------------------------------------ header names
H_AUTH       = "Authorization"
H_TENANT     = "X-Tenant"        # set by the gateway, never trusted by services
H_USER       = "X-User-Id"
H_SESSION    = "X-Session-Id"
H_REQUEST    = "X-Request-Id"

# --------------------------------------------------------------- redis keys
def key_rate_limit(tenant: str, minute: str) -> str:
    return f"rl:{tenant}:{minute}"


def key_cache_version(tenant: str) -> str:
    return f"inv:{tenant}"


def key_query(tenant: str, version: int, digest: str) -> str:
    return f"q:{tenant}:{version}:{digest}"


def key_doc(tenant: str, doc_id: str) -> str:
    return f"d:{tenant}:{doc_id}"


# ------------------------------------------------------------------ s3 keys
def s3_raw_key(tenant: str, doc_id: str) -> str:
    """Tenant-prefixed so a bucket policy can scope access per tenant, and
    so the console is readable when debugging."""
    return f"{tenant}/{doc_id}/raw"


def s3_text_key(tenant: str, doc_id: str) -> str:
    """Extracted text, derived and rebuildable. /raw is immutable; this is a
    cache so a reindex never re-parses the original."""
    return f"{tenant}/{doc_id}/text"
