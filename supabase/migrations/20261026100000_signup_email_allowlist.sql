-- Individual test addresses that may sign up although they are not on a company domain
-- (e.g. a maintainer's personal address for end-to-end trials). Empty in every migration:
-- rows are added by hand on the live project, never committed. Not exposed to the API.
create table public.allowed_signup_emails (
  email text primary key check (email = lower(email) and email ~ '^[^@\s]+@[^@\s]+$'),
  note text not null default '',
  added_at timestamptz not null default now()
);
alter table public.allowed_signup_emails enable row level security;
revoke all on public.allowed_signup_emails from anon, authenticated;

create or replace function public.enforce_signup_domain() returns trigger
language plpgsql security definer set search_path = '' as $$
begin
  if new.email is null or not (
    exists (select 1 from public.allowed_signup_domains d where lower(new.email) like '%@' || d.domain)
    or exists (select 1 from public.allowed_signup_emails e where e.email = lower(new.email))
  ) then
    raise exception 'sign-ups are limited to company email addresses'
      using errcode = '42501';
  end if;
  return new;
end;
$$;
revoke all on function public.enforce_signup_domain() from public, anon, authenticated;
