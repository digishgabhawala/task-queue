"""Tests run against the REAL local Postgres from `supabase start` (Docker),
not a mock/in-memory stand-in -- this is specifically so the FOR UPDATE SKIP
LOCKED claim logic gets genuinely exercised, not something SQLite could ever
verify the same way. Each test gets a clean slate via truncation rather than
transaction-rollback, since task_service's functions own their own commits
(same convention as the other two repos' services)."""
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

# Auth is opt-in per test (see test_auth.py / test_service_keys.py, which
# enable it explicitly). A developer's .env may carry the production auth
# settings; environment variables beat .env, so neutralise them before the
# app's settings load -- otherwise unrelated tests silently run with auth on.
for _key, _value in (("AUTH_ENABLED", "false"), ("AUTH_ADMIN_EMAILS", ""),
                     ("SUPABASE_PUBLISHABLE_KEY", ""), ("SERVICE_API_KEYS", ""),
                     ("WORKER_API_KEYS", ""), ("MACHINE_AUTH_REQUIRED", "false")):
    os.environ[_key] = _value

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
        conn.execute(text("TRUNCATE workers, tasks, artifacts, worker_keys RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def db_session():
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


TEST_DOMAIN = "tq-test.local"
# platform.profiles is owned by ai-bharatiya-web. When that sibling checkout is
# present, apply its real migrations to the LOCAL database (the same way its
# own DB tests do); otherwise create a minimal stand-in.
_PLATFORM_MIGRATIONS = (Path(__file__).resolve().parents[2]
                        / "ai-bharatiya-web" / "db" / "migrations")


def _apply_platform_migrations(conn) -> None:
    conn.exec_driver_sql("create schema if not exists platform")
    conn.exec_driver_sql("create table if not exists platform.schema_migrations ("
                         "version text primary key, applied_at timestamptz not null default now())")
    done = {r[0] for r in conn.exec_driver_sql("select version from platform.schema_migrations")}
    for path in sorted(_PLATFORM_MIGRATIONS.glob("*.sql")):
        if path.stem not in done:
            conn.exec_driver_sql(path.read_text())
            conn.execute(text("insert into platform.schema_migrations (version) values (:v)"),
                         {"v": path.stem})


@pytest.fixture(scope="session")
def _platform_profiles():
    with engine.begin() as conn:
        if _PLATFORM_MIGRATIONS.is_dir():
            _apply_platform_migrations(conn)
        else:
            conn.execute(text("create schema if not exists platform"))
            conn.execute(text("create table if not exists platform.profiles ("
                              "user_id uuid primary key, email text, "
                              "status text not null default 'pending')"))


@pytest.fixture()
def make_profile(_platform_profiles):
    """Make a Supabase user with a platform profile in `status`; returns its id."""
    made = []

    def make(status: str = "approved") -> str:
        with engine.begin() as conn:
            uid = conn.execute(text(
                "insert into auth.users (id, aud, role, email, created_at, updated_at) values "
                "(gen_random_uuid(), 'authenticated', 'authenticated', "
                ":email, now(), now()) returning id::text"),
                {"email": f"{uuid.uuid4().hex[:8]}@{TEST_DOMAIN}"}).scalar_one()
            conn.execute(text("insert into platform.profiles (user_id, email, status) "
                              "values (cast(:u as uuid), 'x', :s) on conflict (user_id) "
                              "do update set status = excluded.status"), {"u": uid, "s": status})
        made.append(uid)
        return uid
    yield make
    with engine.begin() as conn:
        conn.execute(text("delete from platform.profiles where user_id = any(cast(:ids as uuid[]))"),
                     {"ids": made})
        conn.execute(text("delete from auth.users where email like :d"), {"d": f"%@{TEST_DOMAIN}"})
