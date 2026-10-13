-- The queue API is the only public application boundary. These tables live in
-- an exposed schema, so deny Supabase Data API roles direct access and let the
-- FastAPI connection (the table owner) continue to mediate every operation.
alter table public.tasks enable row level security;
alter table public.workers enable row level security;
alter table public.artifacts enable row level security;

-- RLS without policies is default-deny, but grants and policies are separate
-- controls. Remove the legacy table grants as defense in depth.
revoke all privileges on table public.tasks from anon, authenticated;
revoke all privileges on table public.workers from anon, authenticated;
revoke all privileges on table public.artifacts from anon, authenticated;

