"""Tests run against the REAL local Postgres from `supabase start` (Docker),
not a mock/in-memory stand-in -- this is specifically so the FOR UPDATE SKIP
LOCKED claim logic gets genuinely exercised, not something SQLite could ever
verify the same way. Each test gets a clean slate via truncation rather than
transaction-rollback, since task_service's functions own their own commits
(same convention as the other two repos' services)."""
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.db import engine

# Every test TRUNCATEs the queue tables. pydantic-settings reads the repo's
# .env, which may point at the hosted Supabase project -- refuse to run
# rather than wipe production. Override with the local URLs, e.g.
#   DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
for _name, _url in (("DATABASE_URL", settings.database_url),
                    ("SUPABASE_URL", settings.supabase_url)):
    if urlparse(_url).hostname not in _LOCAL_HOSTS:
        pytest.exit(f"{_name} points at a non-local host; tests truncate tables. "
                    "Set it to the local `supabase start` value.", returncode=2)


@pytest.fixture(autouse=True)
def _clean_tables():
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE workers, tasks, artifacts RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def db_session():
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
