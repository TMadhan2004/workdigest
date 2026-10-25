-- pgTAP tests for the WorkDigest schema. Run: `supabase test db` (local stack).
-- Each test id matches the catalog convention (TC-DB-NNN).
begin;
create extension if not exists pgtap with schema extensions;
select plan(36);

-- The test users live at example.test; allow that domain inside this test transaction only.
insert into public.allowed_signup_domains (domain) values ('example.test');

-- Two users. Inserts into auth.users run as the test superuser.
insert into auth.users (id, email, aud, role)
values
  ('11111111-1111-1111-1111-111111111111', 'arjun@example.test', 'authenticated', 'authenticated'),
  ('22222222-2222-2222-2222-222222222222', 'other@example.test', 'authenticated', 'authenticated');

create or replace function pg_temp.act_as(uid uuid) returns void language plpgsql as $$
begin
  perform set_config('role', 'authenticated', true);
  perform set_config('request.jwt.claims', json_build_object('sub', uid, 'role', 'authenticated')::text, true);
end;
$$;

-- TC-DB-001: every table has RLS enabled.
select is(
  (select count(*)::int from pg_tables t join pg_class c on c.relname = t.tablename
   where t.schemaname = 'public' and not c.relrowsecurity),
  0,
  'TC-DB-001: every public table has row-level security enabled'
);

-- TC-DB-002: user_id defaults to the signed-in user.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
insert into public.asks (ask_id, text, kind, opened_on) values ('a1', 'Raise the OTP bug', 'one_time_action', '2026-10-21');
select is(
  (select user_id from public.asks where ask_id = 'a1'),
  '11111111-1111-1111-1111-111111111111'::uuid,
  'TC-DB-002: user_id defaults to auth.uid()'
);

insert into public.days (work_date, rating) values ('2026-10-23', 7);
insert into public.digests (digest_id, work_date, origin, status, text) values ('d1', '2026-10-23', 'generated', 'draft', 'Greetings Priya,');
insert into public.evidence (digest_id, evidence_id, kind, strength, text) values ('d1', 'T1', 'test_run', 'fact', 'billing jest 714/714');
insert into public.manager_replies (reply_id, raw_text, source) values ('r1', 'Hi Arjun, please raise the OTP bug.', 'app');

-- TC-DB-003..007: the other user sees none of it.
select pg_temp.act_as('22222222-2222-2222-2222-222222222222');
select is((select count(*)::int from public.asks), 0, 'TC-DB-003: another user cannot read my asks');
select is((select count(*)::int from public.digests), 0, 'TC-DB-004: another user cannot read my digests');
select is((select count(*)::int from public.evidence), 0, 'TC-DB-005: another user cannot read my evidence');
select is((select count(*)::int from public.manager_replies), 0, 'TC-DB-006: another user cannot read my manager replies');
select is((select count(*)::int from public.days), 0, 'TC-DB-007: another user cannot read my days');

-- TC-DB-008: another user cannot write rows owned by me.
select throws_ok(
  $$insert into public.asks (user_id, ask_id, text, kind, opened_on)
    values ('11111111-1111-1111-1111-111111111111', 'x', 'spoof', 'question', '2026-10-21')$$,
  '42501',
  null,
  'TC-DB-008: inserting a row for someone else is rejected by RLS'
);

-- TC-DB-009: updates and deletes of my rows by another user affect nothing.
update public.asks set status = 'waiting', reason = 'spoof' where ask_id = 'a1';
delete from public.digests where digest_id = 'd1';
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
select is(
  (select status from public.asks where ask_id = 'a1') || '/' || (select count(*)::text from public.digests),
  'open/1',
  'TC-DB-009: another user cannot update or delete my rows'
);

-- TC-DB-010: anon has no access at all.
reset role;
set local role anon;
select throws_ok('select * from public.asks', '42501', null, 'TC-DB-010: anon cannot read any table');
reset role;

-- Constraints (as the owner).
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
select throws_ok(
  $$update public.asks set status = 'done' where ask_id = 'a1'$$,
  '23514', null, 'TC-DB-011: an ask cannot be done without evidence'
);
select throws_ok(
  $$update public.asks set status = 'disagree' where ask_id = 'a1'$$,
  '23514', null, 'TC-DB-012: disagreeing needs a reason'
);
select lives_ok(
  $$update public.asks set status = 'done', evidence = 'https://ei-study.atlassian.net/browse/EIT-9999', updated_by = 'app' where ask_id = 'a1'$$,
  'TC-DB-013: done with evidence is accepted'
);
select throws_ok(
  $$insert into public.days (work_date, rating) values ('2026-10-24', 5)$$,
  '23514', null, 'TC-DB-014: day rating must be 1, 3, 7 or 9'
);
select throws_ok(
  $$insert into public.reply_items (item_id, reply_id, idx, type, text) values ('i1', 'r1', 0, 'chit_chat', 'x')$$,
  '23514', null, 'TC-DB-015: reply item type is restricted to the five kinds'
);

-- TC-DB-016: updated_at moves on update.
update public.days set updated_at = '2000-01-01' where work_date = '2026-10-23';
select ok(
  (select updated_at > '2001-01-01' from public.days where work_date = '2026-10-23'),
  'TC-DB-016: updated_at is maintained by trigger'
);

-- TC-DB-017: deleting a digest removes its evidence and statements.
insert into public.digest_statements (digest_id, section_key, idx, text) values ('d1', 'risks', 0, 'x');
delete from public.digests where digest_id = 'd1';
select is(
  (select count(*)::int from public.evidence) + (select count(*)::int from public.digest_statements),
  0,
  'TC-DB-017: evidence and statements cascade with their digest'
);

-- TC-DB-018: only one active template per user.
insert into public.templates (version, body, active) values ('v1', '{}', true);
select throws_ok(
  $$insert into public.templates (version, body, active) values ('v2', '{}', true)$$,
  '23505', null, 'TC-DB-018: only one active template per user'
);

-- Scheduler.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
insert into public.settings (timezone, nudge_time) values ('Asia/Kolkata', '20:00');
insert into public.days (work_date, status) values ('2026-10-25', 'open');

-- TC-DB-019: the scheduler cannot be called by app users.
select throws_ok(
  $$select public.run_schedule('2026-10-26 14:31:00+00')$$,
  '42501', null, 'TC-DB-019: authenticated users cannot run the scheduler'
);
reset role;

-- 14:31 UTC = 20:01 IST on Mon 26 Oct.
select is(public.run_schedule('2026-10-26 14:31:00+00'), 1, 'TC-DB-020: at 20:01 IST one evening draft job is queued');
select is(public.run_schedule('2026-10-26 14:36:00+00'), 0, 'TC-DB-021: the evening job is queued only once per day');
select is(public.run_schedule('2026-10-26 13:00:00+00'), 0, 'TC-DB-022: nothing is queued before the nudge time (18:30 IST)');
select is(
  (select params ->> 'work_date' from public.generation_jobs where requested_by = 'schedule'),
  '2026-10-26',
  'TC-DB-023: the job targets the local work day'
);
select is(
  (select status from public.days where work_date = '2026-10-25'),
  'closed',
  'TC-DB-024: earlier open days are closed automatically'
);

-- TC-DB-025: the app's live tables are published to Realtime (RLS still filters each change).
select set_eq(
  $$select tablename::text from pg_publication_tables where pubname = 'supabase_realtime' and schemaname = 'public'$$,
  array['devices', 'generation_jobs', 'digests', 'notifications', 'session_summaries', 'asks', 'standing_rules', 'days', 'manager_replies', 'reply_items', 'hidden_sessions', 'pull_requests', 'settings'],
  'TC-DB-025: devices, jobs, digests, notifications, sessions, asks, rules, days, replies, hidden sessions, pull requests and settings are realtime'
);

-- TC-DB-026: a device status outside idle/drafting/syncing/offline is rejected.
select throws_ok(
  $$insert into public.devices (user_id, device_id, kind, status)
    values ('11111111-1111-1111-1111-111111111111', 'mac-x', 'mac', 'sleeping')$$,
  '23514', null, 'TC-DB-026: device status is limited to idle, drafting, syncing and offline'
);

-- TC-DB-027: hidden sessions are private to their owner.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
insert into public.hidden_sessions (session_key, reason) values ('claude:personal', 'personal');
select pg_temp.act_as('22222222-2222-2222-2222-222222222222');
select is((select count(*)::int from public.hidden_sessions), 0, 'TC-DB-027: another user cannot see my hidden sessions');

-- TC-DB-028: my rules start as parsing; an unknown status is rejected.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
insert into public.standing_rules (rule_id, text, check_kind, source, status, updated_by)
values ('mine-1', 'Do not include quiz-service PRs 3-7', 'filter', 'mine', 'parsing', 'app');
select is((select status from public.standing_rules where rule_id = 'mine-1'), 'parsing', 'TC-DB-028: a typed rule is stored as parsing');
select throws_ok(
  $$update public.standing_rules set status = 'maybe' where rule_id = 'mine-1'$$,
  '23514', null, 'TC-DB-029: rule status is limited to parsing, needs_review, active and off'
);
reset role;

-- TC-DB-030: an ask can be cleared; "waiting" ones were moved back to open.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
insert into public.asks (ask_id, text, kind, opened_on) values ('clr-1', 'Share the branch list', 'one_time_action', '2026-10-25');
update public.asks set status = 'cleared', closed_on = '2026-10-25' where ask_id = 'clr-1';
select is((select status from public.asks where ask_id = 'clr-1'), 'cleared', 'TC-DB-030: an open ask can be cleared');
select throws_ok(
  $$update public.asks set status = 'paused' where ask_id = 'clr-1'$$,
  '23514', null, 'TC-DB-031: ask status is limited to open, done, disagree, waiting and cleared'
);
reset role;

-- TC-DB-032..034: sign-ups are limited to allowed email domains.
select lives_ok(
  $$insert into auth.users (id, email, aud, role) values ('33333333-3333-3333-3333-333333333333', 'new.person@ei.study', 'authenticated', 'authenticated')$$,
  'TC-DB-032: a company address can sign up'
);
select throws_ok(
  $$insert into auth.users (id, email, aud, role) values ('44444444-4444-4444-4444-444444444444', 'someone@gmail.com', 'authenticated', 'authenticated')$$,
  '42501', null, 'TC-DB-033: an outside address cannot sign up'
);
select throws_ok(
  $$insert into auth.users (id, email, aud, role) values ('55555555-5555-5555-5555-555555555555', 'x@evil-ei.study.com', 'authenticated', 'authenticated')$$,
  '42501', null, 'TC-DB-034: a look-alike domain cannot sign up'
);

-- TC-DB-035: app users cannot read or change the allowed domains.
select pg_temp.act_as('11111111-1111-1111-1111-111111111111');
select throws_ok($$select * from public.allowed_signup_domains$$, '42501', null, 'TC-DB-035: the domain list is not visible to app users');

-- TC-DB-036: profile fields are per user.
insert into public.settings (manager_name, display_name) values ('Priya', 'Arjun K')
  on conflict (user_id) do update set manager_name = excluded.manager_name, display_name = excluded.display_name;
select is((select manager_name from public.settings), 'Priya', 'TC-DB-036: each user stores their own manager name');
reset role;

select * from finish();
rollback;
