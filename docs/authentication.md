# Supabase authentication

Status: application integration complete; hosted-project activation requires
the administrator user and deployment environment variables below.

## Current scope

Task Queue uses the existing Supabase project as its first human identity
provider. The first milestone deliberately supports one administrator with
email and password:

- There is no public sign-up UI.
- Google and other social providers remain disabled.
- `GET /api/workers`, `GET /api/tasks`, and `GET /api/auth/me` require an
  authenticated administrator when `AUTH_ENABLED=true`.
- Health checks and static assets remain public.
- Producer, worker, artifact, task-submission, claim, and task-result routes
  retain their current MVP credentials and behavior. They are not yet safe for
  untrusted multi-user traffic.

Human browser sessions must never be copied into workers. Worker enrollment
and revocable machine credentials are the next authentication milestone.

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
5. Request `/api/workers` without a bearer token and confirm HTTP `401`.
6. Confirm `/api/health` still returns HTTP `200`.

## Local behavior

Authentication defaults to disabled so existing local workers and tests keep
working. To exercise local auth, create a user through local Supabase Studio,
then set the three variables above using the local publishable/anon key.

The static admin page loads a version-pinned Supabase browser SDK. The browser
receives only the publishable key. The FastAPI backend verifies each presented
access token with Supabase Auth and then applies the administrator allow-list.

## Later milestones

- Add owner IDs to tasks and workers.
- Create one-time worker enrollment and revocable worker credentials.
- Authenticate producers and enforce task ownership.
- Replace public artifact URLs with authorized or short-lived signed access.
- Enable Google inside the same Supabase Auth project when desired; existing
  ownership continues to use the Supabase user ID (`sub`).
- Consider local JWKS verification when traffic justifies removing the
  Supabase user lookup from each protected request.
