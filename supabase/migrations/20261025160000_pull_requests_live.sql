-- Open PRs in the app: live updates, and the tickets each PR is about (so "hide ticket X"
-- rules can hide its PRs in the Timeline, exactly as in the digest).
alter table public.pull_requests add column ticket_keys text[] not null default '{}';
alter table public.pull_requests replica identity full;
alter publication supabase_realtime add table public.pull_requests;
