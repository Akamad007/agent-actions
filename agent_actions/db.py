"""Database engine and session management.

Three persistence modes are supported, selected in priority order:

1. **Injected session factory** — the host application passes its own
   ``sessionmaker`` instance directly.  ``init_db`` is *not* called
   automatically; the host is responsible for ensuring the framework tables
   exist (call ``init_db(engine)`` once at startup, or use Alembic).

2. **Explicit ``db_url``** — a SQLAlchemy URL is provided.  The framework
   creates its own engine, calls ``init_db``, and returns a ``sessionmaker``.
   Works with any SQLAlchemy-supported backend (SQLite, PostgreSQL, MySQL …).

3. **Django auto-detection** — if neither of the above is provided and Django
   is installed with ``settings.configured == True``, the framework extracts
   the ``default`` database URL and creates a SQLAlchemy engine pointed at it.
   The framework tables are created alongside the Django-managed tables.
   Django migrations do **not** manage these tables; use ``init_db`` or a
   separate Alembic env for production.

4. **Standalone fallback** — if none of the above resolves, a local SQLite
   file (``./agent_actions.db``) is used.  This is the zero-config default for
   local development.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from agent_actions.models import Base

logger = logging.getLogger(__name__)

_DEFAULT_URL = "sqlite:///./agent_actions.db"


# ---------------------------------------------------------------------------
# Core helpers (unchanged public API)
# ---------------------------------------------------------------------------


def create_db_engine(db_url: str = _DEFAULT_URL):
    kwargs = {}
    if db_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(db_url, **kwargs)


def init_db(engine) -> None:
    """Create all framework tables if they do not already exist."""
    Base.metadata.create_all(bind=engine)


def make_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_session(session_factory: sessionmaker) -> Generator[Session, None, None]:
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Django URL detection
# ---------------------------------------------------------------------------


def _django_db_to_sqlalchemy_url(db: dict) -> str | None:
    """Convert a Django ``DATABASES['default']`` dict to a SQLAlchemy URL.

    Supports SQLite, PostgreSQL, and MySQL backends.  Returns ``None`` for
    unsupported or incomplete configurations.
    """
    engine = db.get("ENGINE", "")
    name = db.get("NAME", "")
    user = db.get("USER", "")
    password = db.get("PASSWORD", "")
    host = db.get("HOST", "localhost") or "localhost"
    port = str(db.get("PORT", "")) if db.get("PORT") else None

    if "sqlite3" in engine:
        if not name or name == ":memory:":
            return "sqlite:///:memory:"
        return f"sqlite:///{name}"

    if not name:
        return None

    if "postgresql" in engine:
        dialect = "postgresql+psycopg2"
        port = port or "5432"
        if user and password:
            return f"{dialect}://{user}:{password}@{host}:{port}/{name}"
        if user:
            return f"{dialect}://{user}@{host}:{port}/{name}"
        return f"{dialect}://{host}:{port}/{name}"

    if "mysql" in engine:
        dialect = "mysql+pymysql"
        port = port or "3306"
        if user and password:
            return f"{dialect}://{user}:{password}@{host}:{port}/{name}"
        if user:
            return f"{dialect}://{user}@{host}:{port}/{name}"
        return f"{dialect}://{host}:{port}/{name}"

    return None


def _detect_django_db_url() -> str | None:
    """Try to extract the default Django database URL for SQLAlchemy use.

    Returns ``None`` if Django is not installed, not configured, or if the
    backend is not recognised.
    """
    try:
        from django.conf import settings  # type: ignore[import]

        if not settings.configured:
            return None
        db = settings.DATABASES.get("default", {})
        url = _django_db_to_sqlalchemy_url(db)
        if url:
            logger.debug("agent-actions: using Django default database at %s", url)
        return url
    except ImportError:
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# High-level resolver — used by AgentActionApp
# ---------------------------------------------------------------------------


def resolve_session_factory(
    db_url: str | None = None,
    session_factory: sessionmaker | None = None,
) -> sessionmaker:
    """Return a ready-to-use ``sessionmaker`` following the priority rules in
    the module docstring.

    When *session_factory* is provided it is returned as-is.  The caller is
    responsible for ensuring framework tables exist (call ``init_db(engine)``
    at application startup).

    For all other cases the framework resolves a URL, creates its own engine,
    calls ``init_db``, and returns a new ``sessionmaker``.
    """
    if session_factory is not None:
        return session_factory

    resolved_url = db_url or _detect_django_db_url() or _DEFAULT_URL
    engine = create_db_engine(resolved_url)
    init_db(engine)
    return make_session_factory(engine)
