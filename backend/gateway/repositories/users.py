"""Users and tenants. The gateway is the only service that touches users."""
from sqlalchemy import func, select

from common.db import Tenant, User, session


def find_login(email: str) -> dict | None:
    """One query returns the user AND the tenant's current state, so a login
    cannot succeed for a suspended tenant."""
    with session() as s:
        row = s.execute(
            select(User, Tenant)
            .join(Tenant, Tenant.tenant_id == User.tenant_id)
            .where(func.lower(User.email) == email.lower())
        ).first()
        if not row:
            return None
        user, tenant = row
        return {
            "user_id": str(user.user_id),
            "email": user.email,
            "password_hash": user.password_hash,
            "tenant_id": str(tenant.tenant_id),
            "namespace": tenant.namespace,
            "status": tenant.status,
            "rate_limit_rpm": tenant.rate_limit_rpm,
        }


def find_tenant(namespace: str) -> dict | None:
    with session() as s:
        t = s.scalar(select(Tenant).where(Tenant.namespace == namespace))
        if not t:
            return None
        return {
            "tenant_id": str(t.tenant_id),
            "namespace": t.namespace,
            "status": t.status,
            "rate_limit_rpm": t.rate_limit_rpm,
            "index_group": t.index_group,
        }
