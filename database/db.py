"""
database/db.py
──────────────
SQLAlchemy engine setup — SQLite version (no server required).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator

load_dotenv()

# Default: SQLite file inside the project root
_project_root = Path(__file__).resolve().parent.parent
DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{_project_root / 'metro.db'}",
)

# SQLite needs check_same_thread=False for FastAPI's thread-per-request model
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=False,
)

# Enable WAL mode + busy timeout for SQLite so concurrent requests don't deadlock
if DATABASE_URL.startswith("sqlite"):
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")   # 30 s
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ─── FastAPI Dependency ───────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """Yield a database session and ensure it is closed after the request."""
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Table Initialisation ─────────────────────────────────────────────────────
def init_db() -> None:
    """Create all tables. Call once at application startup."""
    from database.models import Base  # noqa: PLC0415
    Base.metadata.create_all(bind=engine)
