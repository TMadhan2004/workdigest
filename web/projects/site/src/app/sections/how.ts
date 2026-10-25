import { isPlatformBrowser } from '@angular/common';
import { AfterViewInit, Component, ElementRef, OnDestroy, PLATFORM_ID, inject, signal, viewChild } from '@angular/core';
import { ModeSwitch, ReadingMode } from '../shared';

interface Stage {
  id: string;
  label: string;
  where: 'sources' | 'mac' | 'cloud' | 'phone';
  simple: string;
  technical: string;
}

export const STAGES: Stage[] = [
  {
    id: 'sources', label: 'Your tools', where: 'sources',
    simple: 'Claude Code, Cursor, git, Jira and Bitbucket: the places your work already happens.',
    technical: 'Agent hooks (SessionStart, UserPromptSubmit, PostToolUse, Stop…), transcripts, git log/branches, Jira REST and Bitbucket 2.0, all read-only.',
  },
  {
    id: 'redact', label: 'Redact', where: 'mac',
    simple: 'Passwords, keys, tokens and emails are blanked out before anything is saved.',
    technical: 'Pattern and entropy rules plus every Keychain secret as a known value; .env, keys and keystores are skipped by path.',
  },
  {
    id: 'evidence', label: 'Evidence', where: 'mac',
    simple: 'The day becomes a list of facts and claims, each one traceable.',
    technical: 'SQLite store with forward-only migrations; an evidence pack of C/T/B/J/R facts and S claims with stable ids.',
  },
  {
    id: 'draft', label: 'AI draft', where: 'mac',
    simple: 'Your own Cursor or Claude writes the wording; WorkDigest writes the facts.',
    technical: 'A JSON-schema prompt to cursor-agent or claude -p; code-built header, Testing/Commits, PRs and closing.',
  },
  {
    id: 'check', label: 'Checks', where: 'mac',
    simple: "Every sentence is checked against the evidence and your manager's rules.",
    technical: 'Grounding flags numbers, tickets and links not in evidence; standing rules and asks produce the gap list.',
  },
  {
    id: 'cloud', label: 'Private cloud', where: 'cloud',
    simple: 'Only the redacted draft, summaries and asks are synced, to an account only you can open.',
    technical: 'Supabase Postgres with row-level security (user_id = auth.uid()), realtime publication, @ei.study sign-ups only.',
  },
  {
    id: 'phone', label: 'Your phone', where: 'phone',
    simple: 'You review, fix and copy the digest, then send it yourself.',
    technical: 'Flutter app with live updates; edits and asks sync back; the Mac applies them on its next 30-second pass.',
  },
];

@Component({
  selector: 'wd-how',
  imports: [ModeSwitch],
  template: `
    <section class="section how" id="how-it-works" aria-labelledby="how-title" #section>
      <header class="section-head">
        <p class="eyebrow">How it works</p>
        <h2 id="how-title">Your Mac does the work. Your phone has the final say.</h2>
        <wd-mode-switch />
      </header>

      <div class="flow" role="list" aria-label="How data moves, step by step">
        <div class="flow-group flow-sources" role="listitem">
          <p class="flow-label">On your Mac: your tools</p>
          <div class="chips">
            @for (tool of tools; track tool) { <span class="flow-chip" [class.lit]="lit() >= 0">{{ tool }}</span> }
          </div>
        </div>
        <div class="flow-link" [class.lit]="lit() >= 1" aria-hidden="true"></div>
        <div class="flow-group flow-mac" role="listitem">
          <p class="flow-label">WorkDigest on your Mac</p>
          <div class="flow-pipeline">
            @for (stage of macStages; track stage.id; let i = $index) {
              <button type="button" class="flow-node" [class.lit]="lit() >= i + 1" [class.focus]="focus() === stage.id"
                      (click)="focus.set(stage.id)" [attr.aria-pressed]="focus() === stage.id">{{ stage.label }}</button>
            }
          </div>
        </div>
        <div class="flow-link" [class.lit]="lit() >= 5" aria-hidden="true"></div>
        <div class="flow-group flow-cloud" role="listitem">
          <p class="flow-label">Your private account</p>
          <button type="button" class="flow-node" [class.lit]="lit() >= 5" [class.focus]="focus() === 'cloud'" (click)="focus.set('cloud')"
                  [attr.aria-pressed]="focus() === 'cloud'">Supabase</button>
        </div>
        <div class="flow-link" [class.lit]="lit() >= 6" aria-hidden="true"></div>
        <div class="flow-group flow-you" role="listitem">
          <p class="flow-label">You</p>
          <button type="button" class="flow-node" [class.lit]="lit() >= 6" [class.focus]="focus() === 'phone'" (click)="focus.set('phone')"
                  [attr.aria-pressed]="focus() === 'phone'">Phone app</button>
        </div>
      </div>

      <ol class="stage-list">
        @for (stage of stages; track stage.id; let i = $index) {
          <li class="glass card stage" [class.focus]="focus() === stage.id" (mouseenter)="focus.set(stage.id)">
            <span class="stage-num" aria-hidden="true">{{ i + 1 }}</span>
            <div>
              <h3>{{ stage.label }}</h3>
              <p>{{ reading.mode() === 'simple' ? stage.simple : stage.technical }}</p>
            </div>
          </li>
        }
      </ol>
    </section>
  `,
})
export class How implements AfterViewInit, OnDestroy {
  protected readonly reading = inject(ReadingMode);
  protected readonly stages = STAGES;
  protected readonly macStages = STAGES.filter((s) => s.where === 'mac');
  protected readonly tools = ['Claude Code', 'Cursor', 'git', 'Jira', 'Bitbucket'];
  /** How far the flow has lit up (−1 = nothing yet, 6 = all the way to the phone). */
  protected readonly lit = signal(-1);
  protected readonly focus = signal('');
  private readonly section = viewChild.required<ElementRef<HTMLElement>>('section');
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  private onScroll?: () => void;

  ngAfterViewInit(): void {
    if (!this.browser) {
      return;
    }
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce) {
      this.lit.set(6);
      return;
    }
    let frame = 0;
    this.onScroll = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const rect = this.section().nativeElement.getBoundingClientRect();
        // 0 when the section top reaches 80% of the screen, 1 when it has scrolled 60% of a screen further.
        const progress = (window.innerHeight * 0.8 - rect.top) / (window.innerHeight * 0.6);
        this.lit.set(Math.max(-1, Math.min(6, Math.floor(progress * 7) - 1)));
      });
    };
    window.addEventListener('scroll', this.onScroll, { passive: true });
    this.onScroll();
  }

  ngOnDestroy(): void {
    if (this.onScroll) window.removeEventListener('scroll', this.onScroll);
  }
}
