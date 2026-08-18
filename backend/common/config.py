"""All configuration comes from the environment. Nothing is hardcoded and
no secret is ever a default — see .env.example."""
import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


class Settings(BaseModel):
    # postgres
    pg_host: str = os.getenv("PG_HOST", "localhost")
    pg_port: int = _int("PG_PORT", 5432)
    pg_db: str = os.getenv("PG_DB", "deeprunner")
    pg_user: str = os.getenv("PG_USER", "deeprunner")
    pg_password: str = os.getenv("PG_PASSWORD", "")

    # elasticsearch
    es_url: str = os.getenv("ES_URL", "http://localhost:9200")
    es_index: str = os.getenv("ES_INDEX", "documents")
    es_shards: int = _int("ES_SHARDS", 2)
    es_replicas: int = _int("ES_REPLICAS", 0)

    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")

    # kafka
    kafka_bootstrap: str = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
    kafka_topic: str = os.getenv("KAFKA_TOPIC", "doc.index.v1")
    kafka_dlq_topic: str = os.getenv("KAFKA_DLQ_TOPIC", "doc.index.dlq")
    kafka_partitions: int = _int("KAFKA_PARTITIONS", 12)
    kafka_group: str = os.getenv("KAFKA_GROUP", "indexer")

    # s3 / minio
    s3_endpoint: str = os.getenv("S3_ENDPOINT", "http://localhost:9000")
    # Presigned URLs are consumed by the BROWSER, not by us. SigV4 signs the
    # Host header, so a URL signed for "minio:9000" cannot be rewritten to
    # localhost — it must be signed against the address the client will use.
    s3_public_endpoint: str = os.getenv("S3_PUBLIC_ENDPOINT",
                                        os.getenv("S3_ENDPOINT", "http://localhost:9000"))
    s3_bucket: str = os.getenv("S3_BUCKET", "documents")
    s3_access_key: str = os.getenv("S3_ACCESS_KEY", "")
    s3_secret_key: str = os.getenv("S3_SECRET_KEY", "")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_presign_ttl: int = _int("S3_PRESIGN_TTL", 60)

    # auth
    jwt_issuer: str = os.getenv("JWT_ISSUER", "deeprunner")
    jwt_audience: str = os.getenv("JWT_AUDIENCE", "deeprunner-api")
    jwt_ttl_seconds: int = _int("JWT_TTL_SECONDS", 900)

    # downstream services (gateway only)
    api_url: str = os.getenv("API_URL", "http://localhost:8081")
    # services fetch the gateway's public key from here to verify tokens
    gateway_internal_url: str = os.getenv("GATEWAY_INTERNAL_URL", "http://localhost:8080")

    # tunables
    inline_body_max_bytes: int = _int("INLINE_BODY_MAX_BYTES", 256 * 1024)
    query_cache_ttl: int = _int("QUERY_CACHE_TTL", 60)
    doc_cache_ttl: int = _int("DOC_CACHE_TTL", 300)
    l1_cache_ttl: int = _int("L1_CACHE_TTL", 5)
    max_page: int = _int("MAX_PAGE", 500)
    max_attempts: int = _int("MAX_ATTEMPTS", 3)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def pg_dsn(self) -> str:
        return (f"postgresql://{self.pg_user}:{self.pg_password}"
                f"@{self.pg_host}:{self.pg_port}/{self.pg_db}")


@lru_cache
def settings() -> Settings:
    return Settings()
