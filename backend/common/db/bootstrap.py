"""Create tables and seed demo tenants. Idempotent, safe to run on every
service start. A real deployment would use Alembic migrations instead.
"""
import bcrypt
from sqlalchemy import select

from .base import Base, engine, session
from .models import Tenant, TenantDomain, User

DEMO_PASSWORD = b"demo"

SEED_TENANTS = [
    # namespace, display,       status,      rpm, domains
    ("acme",    "Acme Corp",   "ACTIVE",     600, ["acme.com", "acme.co.uk"]),
    ("globex",  "Globex Inc",  "ACTIVE",     300, ["globex.com"]),
    # seeded suspended on purpose: proves the middleware status check with a
    # real login that gets 403 rather than a mocked one
    ("initech", "Initech Ltd", "SUSPENDED",   60, ["initech.com"]),
]

SEED_USERS = [
    ("alice@acme.com",    "acme"),
    ("bob@globex.com",    "globex"),
    ("carol@initech.com", "initech"),
]


def create_all() -> None:
    Base.metadata.create_all(engine())


def seed() -> None:
    pw_hash = bcrypt.hashpw(DEMO_PASSWORD, bcrypt.gensalt(rounds=10)).decode()

    with session() as s:
        for ns, display, status, rpm, domains in SEED_TENANTS:
            tenant = s.scalar(select(Tenant).where(Tenant.namespace == ns))
            if tenant is None:
                tenant = Tenant(namespace=ns, display_name=display,
                                status=status, rate_limit_rpm=rpm)
                s.add(tenant)
                s.flush()
            for d in domains:
                if s.get(TenantDomain, d) is None:
                    s.add(TenantDomain(domain=d, tenant_id=tenant.tenant_id))

        for email, ns in SEED_USERS:
            if s.scalar(select(User).where(User.email == email)) is None:
                tenant = s.scalar(select(Tenant).where(Tenant.namespace == ns))
                s.add(User(tenant_id=tenant.tenant_id, email=email,
                           password_hash=pw_hash))


def run() -> None:
    create_all()
    seed()
