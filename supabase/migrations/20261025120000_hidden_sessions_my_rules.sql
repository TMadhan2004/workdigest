-- Hidden sessions (personal work kept out of digests, restorable) and the user's own rules.

-- ---------------------------------------------------------- hidden sessions
create table public.hidden_sessions (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  session_key text not null,
  hidden_at   timestamptz not null default now(),
  reason      text,
  primary key (user_id, session_key)
);

alter table public.hidden_sessions enable row level security;
revoke all on public.hidden_sessions from anon;
create policy owner_all on public.hidden_sessions for all to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

alter table public.hidden_sessions replica identity full;
alter publication supabase_realtime add table public.hidden_sessions;

-- ------------------------------------------------------------- my rules
-- A rule the user typed (source = 'mine') starts as 'parsing'; the Mac reads it into
-- structured filters (check_params) and sets 'needs_review'; the user confirms ('active')
-- or switches it 'off'. Manager-derived and seed rules stay 'active'.
alter table public.standing_rules
  add column status     text not null default 'active'
    check (status in ('parsing', 'needs_review', 'active', 'off')),
  add column updated_by text not null default 'mac'
    check (updated_by in ('mac', 'app')),
  add column created_at timestamptz not null default now();
