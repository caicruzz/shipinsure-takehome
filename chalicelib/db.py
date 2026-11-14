"""Engine and session factory. ``create_all()`` is the whole migration story."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from chalicelib import config
from chalicelib.models import Base

SessionFactory = sessionmaker[Session]

_IN_MEMORY_URLS = {"sqlite://", "sqlite:///:memory:"}


def make_engine(url: str) -> Engine:
    if url in _IN_MEMORY_URLS:
        # One shared connection, otherwise every session sees its own empty database.
        engine = create_engine(url, poolclass=StaticPool, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def make_session_factory(url: str | None = None) -> SessionFactory:
    engine = make_engine(url or config.database_url())
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def dispose(factory: SessionFactory) -> None:
    """Close the factory's connection pool (a temp SQLite file can then be removed)."""
    factory.kw["bind"].dispose()


_default_factory: SessionFactory | None = None


def get_session_factory() -> SessionFactory:
    """Process-wide factory for the Chalice app, created on first use."""
    global _default_factory
    if _default_factory is None:
        _default_factory = make_session_factory()
    return _default_factory


def set_session_factory(factory: SessionFactory | None) -> None:
    """Swap the process-wide factory (tests point the app at a fresh database)."""
    global _default_factory
    _default_factory = factory
