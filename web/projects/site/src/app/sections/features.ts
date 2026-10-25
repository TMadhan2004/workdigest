import { Component, inject } from '@angular/core';
import { ModeSwitch, ReadingMode, Reveal } from '../shared';

interface Feature {
  icon: string;
  title: string;
  simple: string;
  technical: string;
  wide?: boolean;
}

export const FEATURES: Feature[] = [
  {
    icon: '◎',
    title: 'Live Mac status',
    simple: 'The app shows whether your Mac is connected and what it is doing right now, such as drafting or syncing.',
    technical: 'A heartbeat thread upserts the device row every 20 s; the app listens through Supabase realtime and ages it out after 60 s.',
    wide: true,
  },
  {
    icon: '⌁',
    title: 'Your format, learned',
    simple: 'Upload a few past digests and replies once. Your sections, greeting and sign-off become the template.',
    technical: 'Parsed with the digest parser; known sections map to built-in behaviour, unknown ones become custom LLM sections.',
  },
  {
    icon: '✓',
    title: "Your manager's asks",
    simple: 'Paste a reply and its asks become a checklist. Mark each one Done with proof, or Disagree with a reason.',
    technical: 'The reply parser classifies asks, questions and standing rules; repeats count ("raised 3×") and closed asks feed the closing.',
  },
  {
    icon: '◐',
    title: 'Hide personal work',
    simple: 'Swipe a session away and it never appears in a digest. You can bring it back any time.',
    technical: 'Hidden session keys sync to the Mac and are removed from the evidence pack before drafting.',
  },
  {
    icon: '✎',
    title: 'My rules',
    simple: "Write rules in your own words, like “Don't include PRs 3 to 7”. The Mac reads them and you confirm.",
    technical: 'Rules are parsed into code filters (repos, PR numbers, tickets) where possible; the rest are LLM instructions marked best effort.',
  },
  {
    icon: '⇄',
    title: 'Pull requests, tracked',
    simple: 'Your open PRs and how long reviews took, refreshed every 30 minutes.',
    technical: 'Bitbucket API with a read-only token; opened → approved durations computed from activity; My rules exclusions applied.',
  },
  {
    icon: '▦',
    title: 'History calendar',
    simple: 'Every digest you sent, by day, with a month view.',
    technical: 'Digests and their status are stored per work date; the calendar reads a month range in one query.',
  },
  {
    icon: '◷',
    title: 'Your time, your reminders',
    simple: 'Pick when you send your digest. The draft is ready two hours before, and your phone reminds you on weekdays.',
    technical: 'send_deadline drives a pg_cron draft job two hours earlier; the app schedules local notifications 90 minutes before, Monday to Friday.',
  },
  {
    icon: '✦',
    title: 'Your AI, your subscription',
    simple: 'Uses the Cursor or Claude Code you already pay for. If it stops working, you get facts only and an alert.',
    technical: 'cursor-agent / claude -p are probed hourly; a failure falls back to the provider-none path and posts ai_unavailable notifications.',
  },
  {
    icon: '↻',
    title: 'Always up to date',
    simple: 'When a new version is out, your phone and Mac tell you, and updating takes one tap or one command.',
    technical: 'version.json drives a WorkManager check with a one-time notification; APKs are SHA-256 verified; `digest update` reinstalls with uv.',
    wide: true,
  },
];

@Component({
  selector: 'wd-features',
  imports: [ModeSwitch, Reveal],
  template: `
    <section class="section" id="features" aria-labelledby="features-title">
      <header class="section-head">
        <p class="eyebrow">Features</p>
        <h2 id="features-title">Everything the digest needs. Nothing it doesn't.</h2>
        <wd-mode-switch />
      </header>
      <ul class="bento">
        @for (f of features; track f.title; let i = $index) {
          <li wdReveal class="glass card feature" [class.wide]="f.wide" [style.--i]="i % 4">
            <span class="feature-icon" aria-hidden="true">{{ f.icon }}</span>
            <h3>{{ f.title }}</h3>
            <p>{{ reading.mode() === 'simple' ? f.simple : f.technical }}</p>
          </li>
        }
      </ul>
    </section>
  `,
})
export class Features {
  protected readonly reading = inject(ReadingMode);
  protected readonly features = FEATURES;
}
