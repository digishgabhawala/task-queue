"""config.py -- env-driven settings, same pydantic-settings convention as the
sibling repos (character-forge-v2, linkedin-content-agent)."""
from __future__ import annotations

import re

from pydantic import model_validator
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
    # Browser-safe key used by Supabase Auth. This is intentionally separate
    # from the service key above, which must never reach a browser.
    supabase_publishable_key: str = ""
    artifacts_bucket: str = "artifacts"

    # Authentication is opt-in so local workers and existing deployments do
    # not break before the hosted Auth project and Vercel environment are
    # configured. When enabled, the admin allow-list is mandatory.
    auth_enabled: bool = False
    auth_admin_emails: str = ""
    # Machine credentials for apps (e.g. linkedin-content-agent) that read
    # admin-only routes: comma list of "name:sha256-hex-of-key". Only hashes
    # are configured, so the raw key lives solely with the app. Remove an
    # entry to revoke that app.
    service_api_keys: str = ""
    # Separate worker credentials prevent a worker key from submitting tasks
    # or reading producer results. Use one named hash per machine/session.
    worker_api_keys: str = ""
    # Compatibility rollout: missing keys are accepted while false, but any
    # key that is presented is still validated against its route class.
    machine_auth_required: bool = False

    default_lease_minutes: int = 60
    default_retention_hours: int = 48
    artifact_retention_hours: int = 168  # 7 days, more buffer than task retention

    # The worker shares this .env (WORKER_ID, WORKER_NAME, ...); ignore keys
    # the API itself doesn't use instead of refusing to start.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def admin_emails(self) -> frozenset[str]:
        return frozenset(
            email.strip().casefold()
            for email in self.auth_admin_emails.split(",")
            if email.strip()
        )

    @property
    def service_key_hashes(self) -> dict[str, str]:
        """Producer keys as {sha256 hex: service name}."""
        return self._named_key_hashes(self.service_api_keys)

    @property
    def worker_key_hashes(self) -> dict[str, str]:
        """Worker keys as {sha256 hex: worker name}."""
        return self._named_key_hashes(self.worker_api_keys)

    @staticmethod
    def _named_key_hashes(configured: str) -> dict[str, str]:
        hashes = {}
        for entry in configured.split(","):
            if entry.strip():
                name, _, digest = entry.strip().partition(":")
                hashes[digest.strip().lower()] = name.strip()
        return hashes

    @model_validator(mode="after")
    def validate_auth_configuration(self) -> Settings:
        for setting_name, configured in (
            ("SERVICE_API_KEYS", self.service_api_keys),
            ("WORKER_API_KEYS", self.worker_api_keys),
        ):
            for entry in filter(None, (e.strip() for e in configured.split(","))):
                name, _, digest = entry.partition(":")
                if not name.strip() or not re.fullmatch(r"[0-9a-fA-F]{64}", digest.strip()):
                    raise ValueError(
                        f"{setting_name} entries must be name:<sha256 hex of the key>"
                    )
        if self.machine_auth_required and not self.service_key_hashes:
            raise ValueError("SERVICE_API_KEYS is required when MACHINE_AUTH_REQUIRED=true")
        if self.machine_auth_required and not self.worker_key_hashes:
            raise ValueError("WORKER_API_KEYS is required when MACHINE_AUTH_REQUIRED=true")
        if self.auth_enabled and not self.supabase_publishable_key:
            raise ValueError("SUPABASE_PUBLISHABLE_KEY is required when AUTH_ENABLED=true")
        if self.auth_enabled and not self.admin_emails:
            raise ValueError("AUTH_ADMIN_EMAILS is required when AUTH_ENABLED=true")
        return self


settings = Settings()
