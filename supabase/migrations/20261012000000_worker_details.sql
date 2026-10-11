alter table workers add column if not exists details jsonb not null default '{}'::jsonb;

comment on column workers.details is
  'Worker-reported machine status (e.g. memory, ComfyUI state). Opaque to the queue.';
