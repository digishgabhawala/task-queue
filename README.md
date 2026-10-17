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
  queue. Task types today: `image_generation`, `hero_candidate_generation`,
  and `text_generation` (new types require no queue-schema change -- see
  "Task types" below).
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
  until someone else actually wants work of that type. Producers can set
  `lease_minutes` when submitting long-running work; that value is persisted
  with the task and applied when a worker later claims it. A worker may pass
  an explicit claim-time override when it needs different lease semantics.
- **Cleanup via Postgres itself, not application code.** `pg_cron` runs
  `DELETE ... WHERE expires_at < now()` on a schedule (see
  `supabase/migrations/`) -- no cron job to host, no sweep endpoint to call.
- **Artifacts are a separate primitive from task completion.** Binary
  results (images) get uploaded to `POST /artifacts` first, returning an id;
  `POST /tasks/{id}/complete` then just references that id in its (still
  always-JSON) `result`. Keeps task completion uniform regardless of
  whether a given task type produces text or binary output.
- **Workers register and heartbeat through the hosted queue.** A worker
  registers its stable identity and capabilities when it starts, then sends
  a heartbeat once per minute. The admin API/UI considers it offline after
  two minutes without a heartbeat. Busy heartbeats also renew a short rolling
  task lease and report coarse phases such as preparing, rendering, and
  uploading. Exact ComfyUI sampling progress is intentionally out of scope.
  Heartbeats may carry an optional `details` object (≤ 4 KB) describing the
  worker's machine -- the queue stores and returns it without interpreting
  it. Convention used by the image worker (populated by later work):
  `{"memory": {"available_gb": 18.2, "required_gb": {"full": 14, "lightning": 8}},
  "comfyui": {"state": "stopped|starting|ready|restarting|failed", "managed": true}}`.

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

**Humans, producers, and workers use separate credentials.** The admin
dashboard uses a Supabase email/password session plus an administrator
allow-list. Producer apps and workers use independently revocable API-key
classes, so a worker credential cannot submit tasks or read results. See
[authentication](docs/authentication.md). Owner IDs and private artifact URLs
remain future multi-user work, so the service is not yet a tenant-isolated
public API.

## Local development

Needs [Docker](https://docker.com) and the
[Supabase CLI](https://supabase.com/docs/guides/cli) (`brew install
supabase/tap/supabase`).

```bash
supabase start          # local Postgres + Storage, migrations applied automatically
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # includes requirements-worker.txt
.venv/bin/pytest tests/ -v
.venv/bin/uvicorn app.main:app --reload --port 8000
```

`app/config.py`'s defaults already match `supabase start`'s well-known local
values (same connection string/keys on every machine) -- no `.env` needed
for local dev. See `.env.example` for what a real deployment overrides.

The admin dashboard's canonical suite path is `/queue` (for example,
`http://127.0.0.1:8000/queue`). The legacy `/admin` path remains available
for existing bookmarks. It shows registered workers, derived
online/offline state, capabilities, current work, heartbeat recency, recent
tasks, and coarse progress. Its responsive, accessible visual foundation uses
the AI Bharatiyaa Commons v0.2 design tokens pinned in
`app/admin_ui/COMMONS_SOURCE.md`; run `python3 scripts/validate_admin_ui.py`
after UI changes.

The deployed suite dashboard is:
[`https://ai-bharatiya-web.vercel.app/queue`](https://ai-bharatiya-web.vercel.app/queue).

Use the suite URL as the canonical production address in bookmarks and
configuration: `TASK_QUEUE_URL=https://ai-bharatiya-web.vercel.app/queue`.
The client maps that page URL to the product-scoped `/api/queue/*` routes.
The standalone Vercel service URL is an internal upstream used by the suite
gateway, not a public address clients should configure.

Authentication is off by default so local development is not disrupted. A
hosted deployment enables it only after the administrator user exists and
`AUTH_ENABLED`, `AUTH_ADMIN_EMAILS`, and `SUPABASE_PUBLISHABLE_KEY` are set.
Machine authentication uses hashed `SERVICE_API_KEYS` (producer apps) and
`WORKER_API_KEYS` (workers). `MACHINE_AUTH_REQUIRED=false` exists only for a
coordinated rollout; hosted deployments should switch it to `true` after all
clients have their raw keys.

## Deploying schema changes

Apply new migrations to the hosted project **before** the code that uses
them reaches Vercel (`supabase db push`, then push/merge). Deploying first
makes every `workers` query fail until the column exists, so all live
workers get 500s on register/heartbeat.

Database security tests require the local Supabase stack and run with:

```bash
supabase start
supabase db reset
supabase test db
```

The public queue tables have RLS enabled with no browser policies, and direct
grants are revoked from `anon` and `authenticated`. The FastAPI backend remains
the only application boundary; it connects as the table owner and therefore
continues to access the tables because RLS is not forced.

## Trying it end to end (image generation)

```bash
# 1. start the queue server (above), then in another terminal:
WORKER_API_KEY=<raw-worker-key> MOCK_MODE=true python workers/image_worker.py
```

```python
# in a third terminal / notebook
import os
from clients.python_client import TaskQueueClient
client = TaskQueueClient(
    os.environ.get("TASK_QUEUE_URL", "http://127.0.0.1:8000"),
    producer_api_key=os.environ["TASK_QUEUE_API_KEY"],
)

task = client.submit_task("image_generation", {
    "character_id": "gtee_dev", "task": "waving hello", "seed": 42,
    "profile": "full",  # or "lightning"
    # Optional remote locked-character snapshot. When present, any image
    # worker can render it without workspace/gtee_dev/hero.png locally:
    # "hero_asset_id": "sha256:...", "hero_version": 1,
    # "hero_image_url": "https://.../hero.png?v=...",
    # "character_card": {"character_id": "gtee_dev", ...},
})
result = client.wait_for_task(task["id"], timeout_seconds=30)
print(result)  # {"status": "done", "result": {"image_artifact_id": "..."}, ...}

artifact = client.get_artifact(result["result"]["image_artifact_id"])
print(artifact["url"])  # open this -- it's gtee_dev's hero.png in mock mode
```

Set `MOCK_MODE=false` (needs `character-forge-v2` set up with a running
ComfyUI, see that repo's README) to exercise a real render instead of the
instant mock path. The worker forwards the task's explicit `profile` to
forge2 and defaults older payloads without that field to `full`.
When a payload contains the complete remote locked-character snapshot, the
worker caches the hero by asset identity/version and supplies explicit
temporary hero/card/output paths to Character Forge. Payloads without the
snapshot retain the original local-workspace behavior.

Worker environment options:

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_API_KEY` | none | Raw credential for worker-only queue routes |
| `WORKER_ID` | `image-worker-<hostname>` | Stable registration identity |
| `WORKER_NAME` | local hostname | Human-readable admin UI label |
| `HEARTBEAT_SECONDS` | `60` | Periodic heartbeat while idle or busy; state/progress changes also wake it immediately |
| `POLL_SECONDS` | `3` | Short delay after work or a temporary queue error before the next claim attempt |
| `IDLE_POLL_SECONDS` | `60` | Backoff after the queue confirms no matching task is waiting (once the cool-off below has passed) |
| `FAST_POLL_COOLOFF_SECONDS` | `60` | After finishing a task, keep polling every `POLL_SECONDS` this long even if the queue is empty -- callers submit their next step only after the previous one is done; `0` backs off immediately |
| `MOCK_DELAY_SECONDS` | `0` | Mock mode only: pretend rendering takes this long (UI testing) |
| `COMFYUI_URL` | `http://127.0.0.1:8188` | ComfyUI the worker uses/manages |
| `COMFYUI_MANAGE` | `true` | Start ComfyUI on demand (`false`: it must already be running) |
| `COMFYUI_IDLE_MINUTES` | `10` | Stop a worker-started ComfyUI after this long without work |
| `COMFYUI_START_TIMEOUT_SECONDS` | `300` | How long to wait for a started ComfyUI to answer |
| `COMFYUI_START_SCRIPT` | `character-forge-v2/scripts/start_comfyui.sh` | How ComfyUI is launched |
| `COMFYUI_LOG` | `task-queue/comfyui.log` | Output of the worker-started ComfyUI |
| `HERO_CACHE_DIR` | `~/.cache/ai-bharatiya/heroes` | Content-addressed cache for remotely stored locked heroes |
| `POST_IMAGE_FORMAT` | `webp` | Format of uploaded post renders (`image_generation` only): `webp` (quality below, method 6; ~9x smaller than forge2's PNG, accepted by LinkedIn on desktop/iOS/Android), `jpeg` (4:4:4 chroma, optimized) or `png` (forge2's file unchanged). Hero candidates always stay PNG |
| `POST_IMAGE_QUALITY` | `90` | WebP/JPEG quality for post renders |
| `MEMORY_REQUIRED_FULL_GB` | `14` | Free memory a Full render needs, else `insufficient_memory:` |
| `MEMORY_REQUIRED_LIGHTNING_GB` | `0` | Same for Lightning (`0` = never refused) |

**One worker per machine: `workers/local_worker.py`.** Whoever runs the
worker decides what it works on: `WORKER_TASK_TYPES` (comma list, required)
-- it is not guessed from the machine (the worker only warns if, say, Ollama
isn't answering). It runs **one task at a time** and takes the **next
available task** of any configured type -- the queue hands out the oldest,
with no priority between types (smarter scheduling may come later). Memory is handed over explicitly: a model the worker loaded
is unloaded before any render and after `OLLAMA_IDLE_MINUTES` without text
work; its own idle ComfyUI is stopped before text work. A model or ComfyUI
someone else started is never unloaded/stopped. `python
workers/image_worker.py` still works (it starts the same loop).

| Variable | Default | Purpose |
|---|---|---|
| `WORKER_TASK_TYPES` | required (`image_generation` via `image_worker.py`) | e.g. `text_generation,image_generation,hero_candidate_generation`, or just `text_generation` on a laptop without room for images |
| `OLLAMA_URL` | `http://localhost:11434` | Text provider used by the worker |
| `OLLAMA_MODEL` | `qwen3:14b` | Model when the task doesn't name one |
| `OLLAMA_IDLE_MINUTES` | `5` | Unload a worker-loaded model after this long without text work |
| `OLLAMA_TIMEOUT_SECONDS` | `900` | Ceiling for one generation |

### Task types

`image_generation` -- payload `{"character_id", "task", "seed", "profile":
"full"|"lightning"}`, result `{"image_artifact_id"}`; failure prefixes
`insufficient_memory:`, `corrupt_output:`, `comfyui_unavailable:`.

`hero_candidate_generation` -- creates one new character reference without
requiring a pre-existing local Character Forge workspace:

```
payload: {"character_card": {"character_id": "gtee_dev", ...},
          "seed": 1003, "profile": "full"|"lightning"}
result:  {"hero_candidate_artifact_id": "...",
          "candidate": {"character_id": "gtee_dev", "seed": 1003,
                        "prompt": "...", "model_id": "...",
                        "steps": 40, "guidance": 2.5, ...}}
```

The image is uploaded as a queue artifact; `candidate` preserves the
Character Forge sidecar metadata needed to audit or reproduce it. The task
uses the same memory gate and managed ComfyUI lifecycle as post images.

`text_generation` -- provider-neutral, so other providers (e.g. Groq) can be
added worker-side without touching producers:

```
payload: {"messages": [{"role": "system"|"user"|"assistant", "content": "..."}],
          "temperature": 0.2, "reasoning": true, "response_format": "json"|"text",
          "model": null, "deadline_at": "2026-10-11T12:00:00+00:00"}
result:  {"content": "...", "provider": "ollama", "model": "qwen3:14b", "duration_ms": 41230}
```

Failure prefixes: `expired:` (deadline passed before a worker was free --
never run), `text_unavailable:` (provider down / model missing). Producers
should submit with a short `retention_hours` (prompts contain user content).

**The worker manages ComfyUI (real mode).** Start only the worker. When a
task arrives it checks free memory for the requested profile (refusing a Full
render that can't fit, with an `insufficient_memory:` error the producer
shows -- it never downgrades to Lightning itself), starts ComfyUI if needed,
and stops it again after `COMFYUI_IDLE_MINUTES` without work. A blank render
restarts ComfyUI before failing the task (`corrupt_output:`); the retry is the
producer's call. A ComfyUI you started yourself is used but never stopped or
restarted. Ctrl-C / `kill` on the worker also stops the ComfyUI it started.
Heartbeats report `details.memory` and `details.comfyui`.

`workers/image_worker.py` loads the repository's `.env` automatically.
Values supplied by the launching shell take precedence. Keep the heartbeat
at or below 60 seconds with the current two-minute offline threshold.

## Implementation TODO

- [x] Persist stable worker registrations and per-process session IDs.
- [x] Send one-minute worker heartbeats and derive offline status after two
  minutes without one.
- [x] Renew a short rolling task lease while a worker is actively processing
  a claimed task.
- [x] Report coarse task phases (`claimed`, `preparing`, `rendering`,
  `uploading`, and terminal state).
- [x] Provide a deployed, read-only admin dashboard for workers and tasks.
- [x] Add opt-in Supabase authentication and an allow-list for the human admin
  dashboard and broad worker/task listings.
- [x] Worker enrollment with revocable machine credentials: approved users
  create and revoke their own worker keys on /account; a user's key
  registers and claims only as that user's workers.
- [ ] Add owner-aware producer authentication (tasks scoped to the
  submitting owner) and private artifact delivery before this becomes a
  multi-user or externally shared service.
- [x] **Enable Row Level Security on the queue tables** (`tasks`, `workers`,
  `artifacts` in `public`). Without it Supabase's REST API serves them to
  anyone holding the browser-safe publishable key (verified 2026-10-12: a
  task id was readable). RLS has no browser policies, and direct grants are
  revoked from `anon` and `authenticated`; the backend connects as the table
  owner, which bypasses non-forced RLS.
- [x] **Require service keys on the machine routes**: task submit/read and
  artifact routes accept only a producer's `X-API-Key`; worker register,
  heartbeat, claim, complete, fail and artifact upload accept only a worker
  key (separate from producer keys and human sessions).
- [ ] Resume the [Future MVP UI backlog](docs/future-ui-mvp.md) after login,
  authorization, and user management are available.
- [ ] Define retention or manual cleanup for historical worker registrations;
  disappeared workers currently remain visible as offline history.
- [x] Add automated FastAPI route tests for registration, heartbeat, worker
  listing, and the admin page.
- [x] Rerun the Postgres/Storage suite against local Supabase (now 40 tests).
  The suite refuses to run if `.env` points the database at a non-local host.

Exact ComfyUI sampling-step extraction and worker controls such as
pause/drain/shutdown are intentionally not part of the current scope.

## Project layout

```
app/
  main.py, config.py, db.py, models.py, schemas.py, storage.py
  services/task_service.py    -- submit/claim/complete/fail, the queue's core
  services/worker_service.py  -- registration, heartbeat, progress, lease renewal
  services/artifact_service.py
  admin.py                    -- same-origin, read-only admin dashboard route
  admin_ui/                   -- dashboard HTML, CSS, JS, and pinned Commons tokens
  api/routes.py
pyproject.toml's [tool.vercel]  -- points Vercel at app.main:app directly, no separate deploy glue needed
supabase/migrations/           -- schema, storage bucket, pg_cron cleanup jobs
clients/python_client.py       -- reusable by both producers and workers
workers/local_worker.py        -- the one worker per machine: capabilities, claim loop
workers/image_worker.py        -- post-image + hero-candidate handlers (mock or real forge2)
workers/text_worker.py         -- text_generation handler
workers/ollama.py              -- Ollama provider + model memory ownership
workers/comfyui.py             -- ComfyUI supervisor
workers/memory.py              -- free-memory checks
tests/                          -- pytest against real local Postgres (supabase start)
```
