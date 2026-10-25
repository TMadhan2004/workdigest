-- Asks: Done (evidence) or Disagree (reason); "Clear open asks" sets "cleared" (never reported).
-- "waiting" is retired: existing ones become open again.
alter table public.asks drop constraint asks_status_check;
alter table public.asks add constraint asks_status_check
  check (status in ('open', 'done', 'disagree', 'waiting', 'cleared'));
update public.asks set status = 'open' where status = 'waiting';
