from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


def _database_url() -> str:
    """Select the installed PostgreSQL driver explicitly.

    SQLAlchemy's default PostgreSQL driver can vary between releases. The
    project installs psycopg2, so an unqualified URL must resolve to it.
    """
    if settings.database_url.startswith("postgresql://"):
        return settings.database_url.replace(
            "postgresql://", "postgresql+psycopg2://", 1
        )
    return settings.database_url


# Synchronous engine — appropriate for Phase 1
engine = create_engine(
    _database_url(),
    echo=settings.debug,          # prints SQL queries when DEBUG=true
    pool_pre_ping=True,           # verifies connections before use (good for Neon)
)

SessionLocal = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def get_db():
    """FastAPI dependency — yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
