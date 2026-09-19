from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Synchronous engine — appropriate for Phase 1
engine = create_engine(
    settings.database_url,
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
