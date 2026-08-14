-- Generic task queue schema. task_type/payload/result are intentionally
-- opaque (jsonb) -- this table has no knowledge of what any given task_type
-- means, that contract lives between producers and workers, documented
-- per task_type, not enforced by the schema.

create table if not exists tasks (
  id uuid primary key default gen_random_uuid(),
  task_type text not null,
  payload jsonb not null,
  status text not null default 'queued' check (status in ('queued', 'claimed', 'done', 'failed')),
  result jsonb,
  error text,
  claimed_by text,
  claim_token text,
  created_at timestamptz not null default now(),
  claimed_at timestamptz,
  lease_expires_at timestamptz,
  completed_at timestamptz,
  expires_at timestamptz not null
);

-- Supports the claim query: WHERE status='queued' AND task_type = ANY(...)
-- ORDER BY created_at, and the lease-reclaim sweep: WHERE status='claimed'
-- AND lease_expires_at < now().
create index if not exists idx_tasks_claim on tasks (status, task_type, created_at);
create index if not exists idx_tasks_lease on tasks (status, lease_expires_at);
create index if not exists idx_tasks_expires on tasks (expires_at);

create table if not exists artifacts (
  id uuid primary key default gen_random_uuid(),
  content_type text not null,
  size_bytes bigint not null,
  storage_path text not null,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null
);

create index if not exists idx_artifacts_expires on artifacts (expires_at);

-- Storage bucket for artifact bytes. Public read (no auth for MVP1, per
-- explicit decision -- revisit once auth exists) so a worker/producer can
-- fetch an artifact's file directly via its public URL without a signed
-- URL round-trip.
insert into storage.buckets (id, name, public)
values ('artifacts', 'artifacts', true)
on conflict (id) do nothing;

-- Cleanup: no application code, no Cloud Scheduler-equivalent -- pg_cron
-- runs these directly inside Postgres. Requires the pg_cron extension,
-- available on Supabase projects (enable via Database > Extensions in the
-- dashboard, or it's already enabled for the local `supabase start` stack).
create extension if not exists pg_cron;

select cron.schedule(
  'task-queue-cleanup-tasks',
  '*/30 * * * *',
  $$ delete from tasks where expires_at < now() $$
);

select cron.schedule(
  'task-queue-cleanup-artifacts',
  '*/30 * * * *',
  $$ delete from artifacts where expires_at < now() $$
);
