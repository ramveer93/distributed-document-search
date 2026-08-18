"""S3 (MinIO locally). Holds document bodies above the inline threshold.

Presigning is a local HMAC — it makes no network call — which is why the
service can hand out URLs without ever touching the bytes.
"""
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from ..config import settings

_client = None
_presign_client = None


def _build(endpoint: str):
    s = settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=Config(signature_version="s3v4"),
    )


def client():
    """For our own reads and writes, over the internal network."""
    global _client
    if _client is None:
        _client = _build(settings().s3_endpoint)
    return _client


def presign_client():
    """Signs against the PUBLIC address, because the browser is the one that
    will present the URL and the signature covers the Host header."""
    global _presign_client
    if _presign_client is None:
        _presign_client = _build(settings().s3_public_endpoint)
    return _presign_client


def ensure_bucket() -> None:
    bucket = settings().s3_bucket
    try:
        client().head_bucket(Bucket=bucket)
    except ClientError:
        client().create_bucket(Bucket=bucket)


def put(key: str, data: bytes, content_type: str = "text/plain") -> None:
    client().put_object(Bucket=settings().s3_bucket, Key=key, Body=data,
                        ContentType=content_type)


def get(key: str) -> bytes:
    obj = client().get_object(Bucket=settings().s3_bucket, Key=key)
    return obj["Body"].read()


def delete(key: str) -> None:
    client().delete_object(Bucket=settings().s3_bucket, Key=key)


def presigned_url(key: str) -> str:
    """Short TTL: the URL is a credential in its own right, valid even after
    the caller's JWT expires."""
    return presign_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings().s3_bucket, "Key": key},
        ExpiresIn=settings().s3_presign_ttl,
    )


def ping() -> bool:
    try:
        client().head_bucket(Bucket=settings().s3_bucket)
        return True
    except Exception:
        return False
