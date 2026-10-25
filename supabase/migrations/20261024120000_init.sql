-- WorkDigest cloud schema (Phase 4). Forward-only: never edit after it is applied; add a new file.
--
-- What lives here: redacted summaries, digest drafts, evidence lines, manager replies, asks,
-- standing rules, Jira/Bitbucket metadata. Never raw transcripts, prompts or diffs.
-- Every table is per-user with row-level security: a user can only see and change their own rows.
-- The Mac and the app both sign in as the same user; no service-role key is used anywhere.

create extension if not exists pg_cron with schema pg_catalog;
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;

-- ------------------------------------------------------------------ helpers
create or replace function public.touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------- settings
create table public.settings (
  user_id        uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  timezone       text not null default 'Asia/Kolkata',
  nudge_time     time not null default '20:00',
  send_deadline  time not null default '21:00',
  day_boundary_hour smallint not null default 4 check (day_boundary_hour between 0 and 12),
  auto_draft     boolean not null default true,
  provider       text not null default 'cursor_cli' check (provider in ('cursor_cli', 'claude_cli', 'none')),
  transmit       jsonb not null default '{}'::jsonb,
  updated_at     timestamptz not null default now()
);

-- ------------------------------------------------------------------ devices
create table public.devices (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  device_id    text not null,
  kind         text not null check (kind in ('mac', 'phone')),
  label        text,
  app_version  text,
  last_seen_at timestamptz not null default now(),
  primary key (user_id, device_id)
);

-- --------------------------------------------------------------------- days
create table public.days (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  work_date    date not null,
  status       text not null default 'open' check (status in ('open', 'closed')),
  rating       smallint check (rating in (1, 3, 7, 9)),
  project      text,
  team_members text[] not null default '{}',
  counts       jsonb not null default '{}'::jsonb,      -- sessions, commits, tickets, gaps
  closed_at    timestamptz,
  updated_at   timestamptz not null default now(),
  primary key (user_id, work_date)
);

-- ------------------------------------------------------- session summaries
create table public.session_summaries (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  session_key text not null,
  work_date   date not null,
  source      text not null check (source in ('claude_code', 'cursor')),
  title       text,
  repos       text[] not null default '{}',
  first_ts    timestamptz,
  last_ts     timestamptz,
  summary     jsonb not null default '{}'::jsonb,       -- redacted structured summary (claims)
  updated_at  timestamptz not null default now(),
  primary key (user_id, session_key, work_date)
);

-- ----------------------------------------------------------- Jira / Bitbucket
create table public.tickets (
  user_id         uuid not null default auth.uid() references auth.users (id) on delete cascade,
  key             text not null,
  title           text not null,
  status          text,
  status_category text,
  due_date        date,
  parent_key      text,
  url             text not null,
  updated_at      timestamptz not null default now(),
  primary key (user_id, key)
);

create table public.pull_requests (
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  repo              text not null,
  pr_id             integer not null,
  title             text not null,
  url               text not null,
  source_branch     text,
  dest_branch       text,
  state             text not null,
  opened_at         timestamptz not null,
  first_approved_at timestamptz,
  approved_by       text[] not null default '{}',
  declined_at       timestamptz,
  merged_at         timestamptz,
  updated_at        timestamptz not null default now(),
  primary key (user_id, repo, pr_id)
);

-- ------------------------------------------------------------------ digests
create table public.digests (
  user_id          uuid not null default auth.uid() references auth.users (id) on delete cascade,
  digest_id        text not null,
  work_date        date not null,
  origin           text not null check (origin in ('imported', 'generated', 'edited')),
  status           text not null check (status in ('draft', 'final', 'sent_marked')),
  template_version text,
  text             text not null,                     -- WhatsApp text
  structure        jsonb not null default '{}'::jsonb,
  gaps             jsonb not null default '[]'::jsonb,
  provider         text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  primary key (user_id, digest_id)
);
create index digests_by_day on public.digests (user_id, work_date desc, created_at desc);

create table public.digest_statements (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  digest_id    text not null,
  section_key  text not null,
  idx          integer not null,
  text         text not null,
  verified     boolean not null default false,
  evidence_ids text[] not null default '{}',
  problems     text[] not null default '{}',
  primary key (user_id, digest_id, section_key, idx),
  foreign key (user_id, digest_id) references public.digests (user_id, digest_id) on delete cascade
);

create table public.evidence (
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  digest_id   text not null,
  evidence_id text not null,                          -- C1, T2, S3, K1, J1, R1, P1, A1, M1
  kind        text not null,
  strength    text not null check (strength in ('fact', 'claim', 'context')),
  text        text not null,                          -- one redacted line
  refs        jsonb not null default '{}'::jsonb,
  primary key (user_id, digest_id, evidence_id),
  foreign key (user_id, digest_id) references public.digests (user_id, digest_id) on delete cascade
);

-- -------------------------------------------------------- replies and asks
create table public.manager_replies (
  user_id       uuid not null default auth.uid() references auth.users (id) on delete cascade,
  reply_id      text not null,
  for_work_date date,
  raw_text      text not null,
  source        text not null default 'mac' check (source in ('mac', 'app')),
  processed     boolean not null default false,       -- parsed into reply_items by the Mac
  received_at   timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  primary key (user_id, reply_id)
);

create table public.reply_items (
  user_id         uuid not null default auth.uid() references auth.users (id) on delete cascade,
  item_id         text not null,
  reply_id        text not null,
  idx             integer not null,
  type            text not null check (type in ('standing_rule', 'one_time_action', 'question', 'feedback', 'info_only')),
  heading         text,
  text            text not null,
  related_tickets text[] not null default '{}',
  due_date        date,
  status          text not null default 'proposed' check (status in ('proposed', 'confirmed', 'dismissed')),
  method          text not null default 'heuristic',
  updated_by      text not null default 'mac' check (updated_by in ('mac', 'app')),
  updated_at      timestamptz not null default now(),
  primary key (user_id, item_id),
  foreign key (user_id, reply_id) references public.manager_replies (user_id, reply_id) on delete cascade
);

create table public.asks (
  user_id           uuid not null default auth.uid() references auth.users (id) on delete cascade,
  ask_id            text not null,
  text              text not null,
  kind              text not null check (kind in ('one_time_action', 'question')),
  related_tickets   text[] not null default '{}',
  status            text not null default 'open' check (status in ('open', 'done', 'disagree', 'waiting')),
  needs_real_action boolean not null default false,
  evidence          text,
  reason            text,
  opened_on         date not null,
  closed_on         date,
  times_raised      integer not null default 1,
  updated_by        text not null default 'mac' check (updated_by in ('mac', 'app')),
  updated_at        timestamptz not null default now(),
  primary key (user_id, ask_id),
  constraint done_needs_evidence check (status <> 'done' or evidence is not null),
  constraint disagree_needs_reason check (status <> 'disagree' or reason is not null)
);

create table public.standing_rules (
  user_id      uuid not null default auth.uid() references auth.users (id) on delete cascade,
  rule_id      text not null,
  text         text not null,
  check_kind   text not null,
  check_params jsonb not null default '{}'::jsonb,
  source       text not null default 'seed',
  active       boolean not null default true,
  updated_at   timestamptz not null default now(),
  primary key (user_id, rule_id)
);

create table public.templates (
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  version    text not null,
  body       jsonb not null,
  active     boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (user_id, version)
);
create unique index one_active_template on public.templates (user_id) where active;

-- ------------------------------------------------- jobs and notifications
create table public.generation_jobs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  kind        text not null check (kind in ('full', 'section', 'parse_reply', 'sync')),
  params      jsonb not null default '{}'::jsonb,
  status      text not null default 'queued' check (status in ('queued', 'running', 'done', 'failed')),
  result      jsonb,
  error       text,
  requested_by text not null default 'app' check (requested_by in ('app', 'mac', 'schedule')),
  claimed_by  text,
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz,
  updated_at  timestamptz not null default now()
);
create index jobs_queue on public.generation_jobs (user_id, status, created_at);

create table public.notifications (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null default auth.uid() references auth.users (id) on delete cascade,
  kind       text not null check (kind in ('draft_ready', 'nudge', 'mac_offline', 'sync_failed')),
  title      text not null,
  body       text not null,
  payload    jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  read_at    timestamptz
);
create index notifications_unread on public.notifications (user_id, created_at desc) where read_at is null;

-- ----------------------------------------------------- triggers and RLS
do $$
declare
  t text;
begin
  foreach t in array array[
    'settings', 'days', 'session_summaries', 'tickets', 'pull_requests', 'digests',
    'manager_replies', 'reply_items', 'asks', 'standing_rules', 'templates', 'generation_jobs'
  ] loop
    execute format(
      'create trigger touch_%1$s before update on public.%1$I for each row execute function public.touch_updated_at()', t);
  end loop;

  foreach t in array array[
    'settings', 'devices', 'days', 'session_summaries', 'tickets', 'pull_requests', 'digests',
    'digest_statements', 'evidence', 'manager_replies', 'reply_items', 'asks', 'standing_rules',
    'templates', 'generation_jobs', 'notifications'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format(
      'create policy owner_all on public.%I for all to authenticated '
      'using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()))', t);
  end loop;
end;
$$;

-- --------------------------------------------- scheduled draft and day close
-- pg_cron runs in UTC; this runs every 5 minutes and converts to each user's own timezone.
create or replace function public.run_schedule(at_time timestamptz default now()) returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  s record;
  local_now timestamp;
  local_day date;
  queued integer := 0;
begin
  for s in select * from public.settings where auto_draft loop
    local_now := at_time at time zone s.timezone;
    -- The work day ends at the boundary hour, so 01:00 still belongs to the previous day.
    local_day := (local_now - make_interval(hours => s.day_boundary_hour))::date;

    -- Close any earlier day still open.
    update public.days set status = 'closed', closed_at = coalesce(closed_at, at_time)
      where user_id = s.user_id and status = 'open' and work_date < local_day;

    -- At the nudge time, queue one evening draft for today; the Mac picks it up.
    if local_now::time >= s.nudge_time and local_now::time < s.nudge_time + interval '30 minutes'
       and not exists (
         select 1 from public.generation_jobs j
         where j.user_id = s.user_id and j.kind = 'full' and j.requested_by = 'schedule'
           and j.params ->> 'work_date' = local_day::text
       ) then
      insert into public.generation_jobs (user_id, kind, params, requested_by)
        values (s.user_id, 'full', jsonb_build_object('work_date', local_day::text, 'nudge', true), 'schedule');
      queued := queued + 1;
    end if;
  end loop;
  return queued;
end;
$$;

revoke all on function public.run_schedule(timestamptz) from public, anon, authenticated;

select cron.schedule('workdigest-schedule', '*/5 * * * *', $$select public.run_schedule()$$);
