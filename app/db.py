from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

# Deployed connections go through Supabase's transaction-mode pooler
# (Supavisor), which doesn't support server-side prepared statements the way
# a direct connection does -- prepare_threshold=None tells psycopg to never
# promote a query to one. Harmless locally too (just skips a minor
# performance nicety against the un-pooled local Postgres).
#
# NullPool: each Vercel function invocation is its own short-lived process,
# and the pooler is already doing connection pooling server-side -- having
# SQLAlchemy ALSO maintain a client-side pool across invocations that may
# not even persist adds overhead without adding value here.
engine = create_engine(
    settings.database_url,
    poolclass=NullPool,
    pool_pre_ping=True,
    connect_args={"prepare_threshold": None},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
