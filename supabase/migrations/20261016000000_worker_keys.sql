-- Self-service worker keys (user-management spec, Part C). Only the API (the
-- table owner) touches this table, like the other queue tables. The raw key
-- is never stored: only its sha256 hex and its first 8 characters.
create table if not exists public.worker_keys (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  name text not null check (char_length(name) between 1 and 100),
  key_hash text not null unique check (key_hash ~ '^[0-9a-f]{64}$'),
  key_prefix text not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz,
  revoked_at timestamptz
);
create index if not exists worker_keys_user_created_idx on public.worker_keys (user_id, created_at desc);
alter table public.worker_keys enable row level security;
revoke all privileges on table public.worker_keys from anon, authenticated;

alter table public.workers add column if not exists owner_user_id uuid;
comment on column public.workers.owner_user_id is
  'Owner of the worker key used at registration; null for WORKER_API_KEYS keys.';
