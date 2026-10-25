-- Team use: sign-ups only from the company email domain, and per-user profile fields.

-- --------------------------------------------------------- sign-up domain
-- Domains allowed to create accounts. Not exposed to the API (RLS on, no policies).
create table public.allowed_signup_domains (
  domain text primary key check (domain = lower(domain) and domain !~ '@')
);
alter table public.allowed_signup_domains enable row level security;
revoke all on public.allowed_signup_domains from anon, authenticated;
insert into public.allowed_signup_domains (domain) values ('ei.study');

-- Enforced in the database, so it holds even for direct calls to the sign-up API.
create or replace function public.enforce_signup_domain() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.email is null or not exists (
    select 1 from public.allowed_signup_domains d
    where lower(new.email) like '%@' || d.domain
  ) then
    raise exception 'sign-ups are limited to company email addresses'
      using errcode = '42501';
  end if;
  return new;
end;
$$;
revoke all on function public.enforce_signup_domain() from public, anon, authenticated;

create trigger enforce_signup_domain
  before insert on auth.users
  for each row execute function public.enforce_signup_domain();

-- ------------------------------------------------------------ profile
-- Set on the Mac during setup; read by the app ("<manager>'s reply", provider choices).
alter table public.settings
  add column manager_name text not null default '',
  add column display_name text not null default '',
  -- {"cursor_cli": {"state": "ok" | "missing" | "logged_out" | "failing", "detail": "...", "checked_at": "..."}, ...}
  add column capabilities jsonb not null default '{}'::jsonb;

alter table public.settings replica identity full;
alter publication supabase_realtime add table public.settings;

-- ------------------------------------------------------- notifications
-- AI provider stopped / works again (from the Mac's checks), and new versions.
alter table public.notifications drop constraint notifications_kind_check;
alter table public.notifications add constraint notifications_kind_check
  check (kind in ('draft_ready', 'nudge', 'mac_offline', 'sync_failed', 'ai_unavailable', 'ai_restored', 'update_available'));
