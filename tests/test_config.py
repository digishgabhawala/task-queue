import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def _clean_tables():
    """Configuration tests do not need the parent suite's real database."""
    yield


def test_settings_ignore_worker_only_env_keys(tmp_path):
    # The worker shares the repository .env (WORKER_ID, WORKER_NAME, ...);
    # those keys must not stop the API from loading its settings.
    env = tmp_path / ".env"
    env.write_text("WORKER_ID=mac-worker\nWORKER_NAME=Mac\n")
    settings = Settings(_env_file=env)
    assert settings.artifacts_bucket == "artifacts"


def test_auth_requires_publishable_key_and_admin_email():
    with pytest.raises(ValidationError, match="SUPABASE_PUBLISHABLE_KEY"):
        Settings(auth_enabled=True, auth_admin_emails="admin@example.com", _env_file=None)

    with pytest.raises(ValidationError, match="AUTH_ADMIN_EMAILS"):
        Settings(auth_enabled=True, supabase_publishable_key="sb_publishable_test", _env_file=None)


def test_admin_email_list_is_normalized():
    settings = Settings(
        auth_enabled=True,
        supabase_publishable_key="sb_publishable_test",
        auth_admin_emails=" Admin@Example.com, second@example.com ",
        _env_file=None,
    )
    assert settings.admin_emails == frozenset({"admin@example.com", "second@example.com"})
