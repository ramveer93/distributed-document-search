"""The single source of truth for the schema — there is no separate .sql file
to drift from it."""
import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, CheckConstraint, DateTime, ForeignKey,
                        Index, Integer, SmallInteger, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..constants import STATUS_PENDING
from .base import Base


class Tenant(Base):
    """tenant_id is immutable and used only as a foreign key.
    namespace is immutable too, and is the PHYSICAL key — S3 prefix, ES filter
    value, Redis prefix, log lines. Readable, so operations are debuggable.
    """
    __tablename__ = "tenants"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    namespace: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    # escape hatch: promote a whale tenant to its own ES index without a migration
    index_group: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    domains: Mapped[list["TenantDomain"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan")


class TenantDomain(Base):
    """Domains change, and a tenant usually has several, so they are not a
    column on tenants. Read only at login, never on the request path."""
    __tablename__ = "tenant_domains"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="domains")


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship(back_populates="users")


class Document(Base):
    """status: PENDING -> LIVE | FAILED,  and any -> DELETED (soft).

    Keyed on (tenant, doc_id) so every lookup is naturally tenant-scoped —
    a wrong-tenant read returns no rows rather than relying on a handler
    remembering to filter.
    """
    __tablename__ = "documents"

    tenant: Mapped[str] = mapped_column(
        String(64), ForeignKey("tenants.namespace"), primary_key=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # exactly one of these is set — see the CheckConstraint below
    body: Mapped[str | None] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128), default="text/plain")
    byte_size: Mapped[int] = mapped_column(BigInteger, default=0)
    doc_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_PENDING)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        CheckConstraint("body IS NOT NULL OR s3_key IS NOT NULL",
                        name="body_or_s3_key"),
        Index("documents_recent", "tenant", updated_at.desc()),
        # partial: the ops backlog is tiny compared to the table
        Index("documents_ops_queue", "tenant", "status",
              postgresql_where=status != "LIVE"),
        Index("documents_metadata", doc_metadata,
              postgresql_using="gin", postgresql_ops={"metadata": "jsonb_path_ops"}),
    )


class IndexOutbox(Base):
    """Written in the SAME transaction as the document row.

    Either both land or neither does, so a crash can never leave a document
    that nothing downstream knows about. Production replaces this table with
    Debezium reading the WAL; the relay disappears, the guarantee does not.
    """
    __tablename__ = "index_outbox"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant: Mapped[str] = mapped_column(String(64), nullable=False)
    op: Mapped[str] = mapped_column(String(16), nullable=False)   # UPSERT | DELETE
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # carries the trace across the queue, so one grep follows a document from
    # HTTP request through to indexed
    request_id: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("index_outbox_unpublished", "seq",
              postgresql_where=published_at.is_(None)),
    )
