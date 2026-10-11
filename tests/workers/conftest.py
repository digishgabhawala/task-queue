"""Worker tests are pure unit tests: override the parent conftest's autouse
DB-truncating fixture so they run without `supabase start` (Docker costs RAM
that real renders need)."""
import pytest


@pytest.fixture(autouse=True)
def _clean_tables():
    yield
