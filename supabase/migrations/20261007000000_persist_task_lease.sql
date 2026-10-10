-- Persist the lease requested by a producer so it is still available when a
-- worker claims the task later. Previously the API accepted lease_minutes but
-- discarded it, causing every normal worker claim to use the 60-minute global
-- default even for image tasks that explicitly requested 240 minutes.

alter table tasks add column if not exists lease_minutes integer;

update tasks
set lease_minutes = 60
where lease_minutes is null;

alter table tasks alter column lease_minutes set default 60;
alter table tasks alter column lease_minutes set not null;

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'tasks_lease_minutes_positive'
      and conrelid = 'tasks'::regclass
  ) then
    alter table tasks
      add constraint tasks_lease_minutes_positive check (lease_minutes > 0);
  end if;
end
$$;

comment on column tasks.lease_minutes is
  'Producer-requested claim duration; used unless a worker explicitly overrides it.';
