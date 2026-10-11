create table if not exists workers (
  id text primary key,
  name text not null,
  session_id text not null,
  capabilities jsonb not null default '{}'::jsonb,
  version text,
  state text not null default 'idle' check (state in ('idle', 'busy')),
  current_task_id uuid references tasks(id) on delete set null,
  registered_at timestamptz not null default now(),
  session_started_at timestamptz not null default now(),
  last_heartbeat_at timestamptz not null default now()
);

create index if not exists idx_workers_last_heartbeat
  on workers (last_heartbeat_at desc);

alter table tasks add column if not exists progress_stage text;
alter table tasks add column if not exists progress_message text;
alter table tasks add column if not exists progress_current integer;
alter table tasks add column if not exists progress_total integer;
alter table tasks add column if not exists progress_updated_at timestamptz;

comment on table workers is
  'Registered task workers. Online status is derived from last_heartbeat_at.';
comment on column tasks.progress_stage is
  'Worker-reported coarse progress phase. Task payload remains opaque to the queue.';
