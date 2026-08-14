# What's been verified, and where to see it

## Automated tests

```bash
supabase start
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ruff check .
.venv/bin/pytest tests/ -v
```

`tests/` runs against a **real local Postgres** (`supabase start`'s Docker
stack), not a mock or in-memory stand-in -- specifically so the
`FOR UPDATE SKIP LOCKED` claim logic in `task_service.claim_next()` gets
genuinely exercised: two sequential claims against one queued task, claim
after an expired lease gets reclaimed, a stale `claim_token` from a
reclaimed task correctly fails to complete/fail. `test_artifact_service.py`
uploads/downloads real bytes through the local Supabase Storage API.

**What automated tests don't cover yet**: the HTTP routes themselves
(`app/api/routes.py`) -- tests exercise the service layer directly, not
through FastAPI's `TestClient`. The end-to-end path (real HTTP submit ->
claim -> complete -> artifact retrieval, and the `image_worker.py` mock-mode
loop specifically) is verified live instead, see below.

## Verified live

- [ ] Schema migration applies cleanly on a fresh `supabase start` --
      confirmed: `tasks`/`artifacts` tables, indexes, the `artifacts`
      storage bucket, and both `pg_cron` cleanup jobs all present.
- [ ] Full pytest suite passing against local Postgres
- [ ] End-to-end via HTTP: submit an `image_generation` task, run
      `image_worker.py` in `MOCK_MODE`, confirm it claims, completes, and
      the resulting artifact is fetchable at its public URL
- [ ] Real (non-mock) render through `character-forge-v2`
- [ ] Deployed to Vercel + a real Supabase project, same tests pass against it

(Checkboxes get filled in as each is actually run -- this file tracks
reality, not aspiration.)
