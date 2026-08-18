"""Engine and session handling.

pool_pre_ping keeps a restarted Postgres from handing every worker a dead
connection — cheap, and it removes a whole class of "first request after a
deploy fails" reports.
"""
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_Session: sessionmaker | None = None


def engine():
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(
            settings().pg_dsn.replace("postgresql://", "postgresql+psycopg://"),
            pool_size=5, max_overflow=5, pool_pre_ping=True, future=True,
        )
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


@contextmanager
def session() -> Iterator[Session]:
    """Commits on clean exit, rolls back on any exception.

    The document row and its outbox row share this transaction — that single
    fact is what makes indexing lossless.
    """
    engine()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def ping() -> bool:
    from sqlalchemy import text
    try:
        with engine().connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
