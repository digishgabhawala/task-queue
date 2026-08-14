"""Tests run against the REAL local Postgres from `supabase start` (Docker),
not a mock/in-memory stand-in -- this is specifically so the FOR UPDATE SKIP
LOCKED claim logic gets genuinely exercised, not something SQLite could ever
verify the same way. Each test gets a clean slate via truncation rather than
transaction-rollback, since task_service's functions own their own commits
(same convention as the other two repos' services)."""
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.db import engine


@pytest.fixture(autouse=True)
def _clean_tables():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE tasks, artifacts RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def db_session():
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
