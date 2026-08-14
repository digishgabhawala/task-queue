# task-queue

A generic, provider-agnostic task queue: producers submit work, workers pull
it, execute it wherever they happen to be running, and report results back
-- decoupling *where compute happens* from *where the request came from*.

Built to solve a concrete problem: running ComfyUI (image generation) and
Ollama (text generation) in the same process/machine competes for limited
RAM/VRAM and has repeatedly caused crashes (see the sibling repos'
`character-forge-v2`/`linkedin-content-agent` history). This queue lets each
heavy workload run on whichever machine is actually free to do it -- your
laptop, a friend's, a Colab session -- without the producer needing to know
or care which.

## Design

- **Generic on purpose.** The `tasks` table has no idea what `task_type`,
  `payload`, or `result` mean -- that contract lives between a producer and
  a worker for a given task type, documented per type, not enforced by the
  queue. Today's task type is `image_generation`; adding a second (e.g.
  `text_generation`) later means zero changes to this repo's own code.
- **Pull-based, both directions.** Workers poll for available work; producers
  poll for their task's result. No callbacks -- a callback would require the
  caller to be inbound-reachable, which won't hold once producers themselves
  might run behind NAT too (a laptop, not just a hosted backend).
- **Postgres `FOR UPDATE SKIP LOCKED` for claiming.** The standard Postgres
  job-queue idiom -- two workers polling at the same instant can't claim the
  same task, by construction, no application-level locking needed.
- **Claim tokens.** Completing/failing a task requires the `claim_token`
  handed back at claim time. If a worker stalls past its lease and the task
  gets reclaimed by someone else, the original worker's stale token no
  longer works -- closes off a real race, not just a theoretical one.
- **Lease/reclaim without a background process.** A claimed task past its
  `lease_expires_at` gets reset to `queued` lazily, right before the next
  claim attempt -- no scheduler needed, since nobody needs it reclaimed
  until someone else actually wants work of that type.
- **Cleanup via Postgres itself, not application code.** `pg_cron` runs
  `DELETE ... WHERE expires_at < now()` on a schedule (see
  `supabase/migrations/`) -- no cron job to host, no sweep endpoint to call.
- **Artifacts are a separate primitive from task completion.** Binary
  results (images) get uploaded to `POST /artifacts` first, returning an id;
  `POST /tasks/{id}/complete` then just references that id in its (still
  always-JSON) `result`. Keeps task completion uniform regardless of
  whether a given task type produces text or binary output.

## Stack

FastAPI (deployed to Vercel, which auto-detects `app/main.py`'s `app` and
routes every request to it directly -- see `[tool.vercel]` in
`pyproject.toml`; no `vercel.json` needed) + Postgres and Storage via
Supabase. Chosen
over a self-hosted VM specifically to avoid babysitting infrastructure for
what's meant to be genuinely idle most of the time; chosen over Firestore
specifically because `FOR UPDATE SKIP LOCKED` is a cleaner, more
battle-tested claim primitive than a Firestore transaction, and because
Postgres is a better fit for the team's existing SQLAlchemy conventions
(see the sibling repos).

**No authentication yet.** Deliberate MVP1 scope -- revisit before this
carries real multi-user traffic. Endpoints are open by design for now.

## Local development

Needs [Docker](https://docker.com) and the
[Supabase CLI](https://supabase.com/docs/guides/cli) (`brew install
supabase/tap/supabase`).

```bash
supabase start          # local Postgres + Storage, migrations applied automatically
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest tests/ -v
.venv/bin/uvicorn app.main:app --reload --port 8000
```

`app/config.py`'s defaults already match `supabase start`'s well-known local
values (same connection string/keys on every machine) -- no `.env` needed
for local dev. See `.env.example` for what a real deployment overrides.

## Trying it end to end (image generation)

```bash
# 1. start the queue server (above), then in another terminal:
MOCK_MODE=true python workers/image_worker.py
```

```python
# in a third terminal / notebook
from clients.python_client import TaskQueueClient
client = TaskQueueClient("http://127.0.0.1:8000")

task = client.submit_task("image_generation", {
    "character_id": "gtee_dev", "task": "waving hello", "seed": 42,
})
result = client.wait_for_task(task["id"], timeout_seconds=30)
print(result)  # {"status": "done", "result": {"image_artifact_id": "..."}, ...}

artifact = client.get_artifact(result["result"]["image_artifact_id"])
print(artifact["url"])  # open this -- it's gtee_dev's hero.png in mock mode
```

Set `MOCK_MODE=false` (needs `character-forge-v2` set up with a running
ComfyUI, see that repo's README) to exercise a real render instead of the
instant mock path.

## Project layout

```
app/
  main.py, config.py, db.py, models.py, schemas.py, storage.py
  services/task_service.py    -- submit/claim/complete/fail, the queue's core
  services/artifact_service.py
  api/routes.py
pyproject.toml's [tool.vercel]  -- points Vercel at app.main:app directly, no separate deploy glue needed
supabase/migrations/           -- schema, storage bucket, pg_cron cleanup jobs
clients/python_client.py       -- reusable by both producers and workers
workers/image_worker.py        -- claims image_generation tasks (mock or real forge2)
tests/                          -- pytest against real local Postgres (supabase start)
```
