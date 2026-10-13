begin;

create extension if not exists pgtap with schema extensions;

select plan(33);

-- Every queue table in the exposed public schema must have RLS enabled.
select ok(
  c.relrowsecurity,
  format('RLS is enabled on public.%I', expected.table_name)
)
from (values ('tasks'), ('workers'), ('artifacts')) as expected(table_name)
join pg_namespace n on n.nspname = 'public'
join pg_class c on c.relnamespace = n.oid and c.relname = expected.table_name;

-- Direct browser access is intentionally unsupported, so there must be no
-- policy that grants rows back to a Data API role.
select is(
  (
    select count(*)
    from pg_policies p
    where p.schemaname = 'public' and p.tablename = expected.table_name
  ),
  0::bigint,
  format('public.%I has no direct-access policies', expected.table_name)
)
from (values ('tasks'), ('workers'), ('artifacts')) as expected(table_name);

-- Supabase's publishable key assumes anon or authenticated. Neither role may
-- retain a direct CRUD grant, independently of the RLS default-deny policy.
select ok(
  not has_table_privilege(
    expected.role_name,
    format('public.%I', expected.table_name),
    expected.privilege_name
  ),
  format(
    '%I cannot %s public.%I',
    expected.role_name,
    lower(expected.privilege_name),
    expected.table_name
  )
)
from (
  select role_name, table_name, privilege_name
  from (values ('anon'), ('authenticated')) as roles(role_name)
  cross join (values ('tasks'), ('workers'), ('artifacts')) as tables(table_name)
  cross join (values ('SELECT'), ('INSERT'), ('UPDATE'), ('DELETE')) as privileges(privilege_name)
) as expected;

-- The FastAPI deployment connects as the table owner. Enabling (but not
-- forcing) RLS must leave that server-side path operational.
select lives_ok(
  format('select count(*) from public.%I', expected.table_name),
  format('table owner can still read public.%I', expected.table_name)
)
from (values ('tasks'), ('workers'), ('artifacts')) as expected(table_name);

select * from finish();
rollback;

