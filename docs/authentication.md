# Supabase authentication

Status: human authentication is active; separate producer and worker machine
credentials are implemented with a compatibility switch for staged rollout.

## Current scope

Task Queue uses the existing Supabase project as its first human identity
provider. The first milestone deliberately supports one administrator with
email and password:

- There is no public sign-up UI.
- Google and other social providers remain disabled.
- `GET /api/workers`, `GET /api/tasks`, and `GET /api/auth/me` require an
  authenticated administrator when `AUTH_ENABLED=true`.
- Health checks and static assets remain public.
- Machine routes use `X-API-Key`, with different key sets for producers and
  workers. Human bearer sessions do not authorize worker routes.

This establishes role separation but not tenant isolation. Tasks do not yet
have owner IDs, and artifact URLs are still public once known.

## Machine API keys

Each machine client keeps one raw secret in its own environment. The queue API
stores only SHA-256 hashes and compares the hash of each presented key using a
constant-time comparison.

- Producer apps use raw `TASK_QUEUE_API_KEY` values whose hashes are listed in
  server-side `SERVICE_API_KEYS`. Producers may submit a task, fetch one task,
  fetch one artifact, and read worker availability. `GET /api/workers` also
  accepts a human admin session for the dashboard.
- Workers use raw `WORKER_API_KEY` values whose hashes are listed separately
  in server-side `WORKER_API_KEYS`. Workers may register, heartbeat, claim,
  complete/fail, and upload artifacts.
- A producer key is rejected from worker routes and a worker key is rejected
  from producer routes. Removing a named hash and redeploying revokes it.
- Both `/api/*` and `/api/queue/*` mounts apply the same dependencies.

Create an independent key for each app or worker:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # raw key -> that client's .env
python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" '<raw key>'
```

The API environment format is a comma-separated name/hash map:

```text
SERVICE_API_KEYS=linkedin-content-agent:<producer-hash>,another-app:<producer-hash>
WORKER_API_KEYS=digish-macbook:<worker-hash>,colab-worker:<worker-hash>
MACHINE_AUTH_REQUIRED=true
```

Malformed entries, or enforcement without both key classes, fail closed at
startup. During a coordinated migration only, set
`MACHINE_AUTH_REQUIRED=false`: a missing key is temporarily accepted, while a
present but invalid/wrong-role key is still rejected. Give every client its
raw key before switching the server to `true`.

## One-time hosted setup

Complete these steps in this order to avoid publishing a login page before its
only account exists.

1. Open the existing Task Queue project in the Supabase dashboard.
2. Under **Authentication → Users**, create the administrator user with the
   intended email and a generated unique password. Mark the email confirmed.
3. Under **Authentication → Providers**, keep Email enabled and Google
   disabled.
4. Under the Authentication sign-up settings, disable creation of new users.
   User creation through the administrative dashboard remains available.
5. Under **Project Settings → API Keys**, copy the browser-safe publishable
   key. A legacy `anon` key also works during Supabase's key migration. Never
   expose or substitute the `service_role`/secret key.
6. Add these Vercel Production environment variables:

   ```text
   AUTH_ENABLED=true
   AUTH_ADMIN_EMAILS=<the exact administrator email>
   SUPABASE_PUBLISHABLE_KEY=<sb_publishable_... or legacy anon key>
   ```

   `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` should already exist. Keep the
   service key server-only.
7. Redeploy after all variables are present.

`AUTH_ADMIN_EMAILS` accepts a comma-separated list if a second administrator is
needed later. Authentication fails closed at application startup when auth is
enabled without a publishable key or administrator email.

## Verification

After deployment:

1. Open `/admin` in a private browser window. The queue dashboard must not be
   visible before sign-in.
2. Confirm an incorrect password shows a generic error and no account details.
3. Sign in with the created administrator and verify workers/tasks load.
4. Sign out and confirm protected data disappears.
5. Request each machine route without `X-API-Key` and confirm HTTP `401`.
6. Confirm producer and worker keys work only on their respective routes.
7. Confirm `/api/health` still returns HTTP `200`.

## Local behavior

Authentication defaults to disabled so existing local workers and tests keep
working. Machine authentication also defaults to compatibility mode locally.
To exercise the production posture, configure both hashed key sets and set
`MACHINE_AUTH_REQUIRED=true`; give the producer client `TASK_QUEUE_API_KEY`
and the worker `WORKER_API_KEY`.

The static admin page loads a version-pinned Supabase browser SDK. The browser
receives only the publishable key. The FastAPI backend verifies each presented
access token with Supabase Auth and then applies the administrator allow-list.

## Later milestones

- Add owner IDs to tasks and workers.
- Create one-time worker enrollment/rotation rather than manual key setup.
- Add producer identity and enforce task ownership.
- Replace public artifact URLs with authorized or short-lived signed access.
- Enable Google inside the same Supabase Auth project when desired; existing
  ownership continues to use the Supabase user ID (`sub`).
- Consider local JWKS verification when traffic justifies removing the
  Supabase user lookup from each protected request.
