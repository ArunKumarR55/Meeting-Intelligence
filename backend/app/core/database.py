from collections.abc import Generator
from pathlib import Path

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


def _sqlite_database_url(database_path: Path) -> URL:
    """Build a SQLAlchemy SQLite URL from a cross-platform filesystem path."""
    absolute_path = database_path.expanduser().resolve()
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    return URL.create(
        drivername="sqlite+pysqlite",
        database=str(absolute_path),
    )


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""


engine = create_engine(
    _sqlite_database_url(settings.database_path),
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: object, connection_record: object) -> None:
    """Enable SQLite foreign-key constraints on every new pooled connection."""
    del connection_record
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    class_=Session,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    """Yield a database session and always close it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
