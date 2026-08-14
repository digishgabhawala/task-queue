"""config.py -- env-driven settings, same pydantic-settings convention as the
sibling repos (character-forge-v2, linkedin-content-agent)."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Local default matches `supabase start`'s well-known local Postgres port/
    # credentials (documented, same on every machine) -- real deployments
    # override via a Vercel-configured env var, never committed here.
    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:54322/postgres"

    # Supabase Storage -- same story: local `supabase start` values by
    # default, overridden by real project values at deploy time.
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_service_key: str = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImV4cCI6MTk4MzgxMjk5Nn0."
        "EGIM96RAZx35lJzdJsyH-qQwv8Hdp7fsn3W0YpN81IU"
    )  # supabase start's well-known local demo service_role key, same on every machine -- not a secret
    artifacts_bucket: str = "artifacts"

    default_lease_minutes: int = 60
    default_retention_hours: int = 48
    artifact_retention_hours: int = 168  # 7 days, more buffer than task retention

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
