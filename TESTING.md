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

- [x] Schema migration applies cleanly on a fresh `supabase start` --
      confirmed: `tasks`/`artifacts` tables, indexes, the `artifacts`
      storage bucket, and both `pg_cron` cleanup jobs all present.
- [x] Full pytest suite (19 cases) passing against local Postgres
- [x] End-to-end via HTTP, local stack: submit an `image_generation` task,
      run `image_worker.py` in `MOCK_MODE`, confirm it claims, completes,
      and the resulting artifact is byte-identical to
      `character-forge-v2/workspace/gtee_dev/hero.png` when fetched from
      its public Supabase Storage URL.
- [x] Migration applied to a real Supabase project (not just local) --
      same tables/bucket/cron jobs confirmed via direct `psycopg` connection.
- [x] Full pytest suite passing against the real Supabase Postgres (same
      19 cases, ~98s instead of <1s -- real network round-trips to
      ap-northeast-1, not a meaningful concern for a queue this size).
- [x] Deployed to Vercel, `/api/health` reachable publicly. Found and fixed
      live: Vercel's Python runtime now auto-detects a FastAPI entrypoint
      and routes every request to it directly -- the original
      `vercel.json` rewrite-everything-to-`/api/index` setup (the
      pre-auto-detection pattern) actively conflicted with that, collapsing
      every request path to the literal rewrite destination before FastAPI
      ever saw it, so every route 404'd. Fixed by removing the manual
      rewrite and declaring `[tool.vercel] entrypoint = "app.main:app"`
      explicitly in `pyproject.toml`.
- [x] Full end-to-end against the live Vercel deployment + real Supabase:
      submitted a task via the public URL, a worker running on a completely
      separate machine claimed and completed it, the resulting artifact
      fetched from the public internet is byte-identical to `hero.png`.
      This is the actual target shape working for real -- a coordinator
      reachable from anywhere, a worker running wherever compute happens
      to be free.
- [ ] Real (non-mock) render through `character-forge-v2` submitted via
      the queue (mock mode covers the queue mechanics; a real render is
      still a manual `forge2 generate` call today, not yet wired through
      `image_worker.py`'s non-mock path in a live test)

(Checkboxes get filled in as each is actually run -- this file tracks
reality, not aspiration.)
