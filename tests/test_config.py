from app.config import Settings


def test_settings_ignore_worker_only_env_keys(tmp_path):
    # The worker shares the repository .env (WORKER_ID, WORKER_NAME, ...);
    # those keys must not stop the API from loading its settings.
    env = tmp_path / ".env"
    env.write_text("WORKER_ID=mac-worker\nWORKER_NAME=Mac\n")
    settings = Settings(_env_file=env)
    assert settings.artifacts_bucket == "artifacts"
