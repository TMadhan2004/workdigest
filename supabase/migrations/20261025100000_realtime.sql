-- Realtime for the app: live Mac status, job progress, drafts, notifications and lists.
--
-- Tables join the supabase_realtime publication; Realtime applies each table's RLS
-- (owner_all) before delivering a change, so a user only ever receives their own rows.

-- What the Mac is doing right now, for "Mac is drafting…" in the app.
alter table public.devices
  add column status      text not null default 'idle'
    check (status in ('idle', 'drafting', 'syncing', 'offline')),
  add column current_job uuid;

-- Full old rows on UPDATE/DELETE, so the app can tell which row changed.
alter table public.devices           replica identity full;
alter table public.generation_jobs   replica identity full;
alter table public.digests           replica identity full;
alter table public.notifications     replica identity full;
alter table public.session_summaries replica identity full;
alter table public.asks              replica identity full;
alter table public.standing_rules    replica identity full;
alter table public.days              replica identity full;
alter table public.manager_replies   replica identity full;
alter table public.reply_items       replica identity full;

alter publication supabase_realtime add table
  public.devices,
  public.generation_jobs,
  public.digests,
  public.notifications,
  public.session_summaries,
  public.asks,
  public.standing_rules,
  public.days,
  public.manager_replies,
  public.reply_items;
