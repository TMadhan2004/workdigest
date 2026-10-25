import { isPlatformBrowser } from '@angular/common';
import { AfterViewInit, Component, ElementRef, OnDestroy, PLATFORM_ID, inject, signal, viewChildren } from '@angular/core';
import { Phone, Screen } from '../phone';
import { ModeSwitch, ReadingMode } from '../shared';

interface Step {
  screen: Screen;
  time: string;
  title: string;
  simple: string;
  technical: string;
}

export const STEPS: Step[] = [
  {
    screen: 'capture',
    time: 'All day',
    title: 'It notices your work, quietly',
    simple:
      'While you work, WorkDigest keeps a private diary on your Mac: what you asked Claude or Cursor to do, the commits you made, the tests that passed, and your tickets and pull requests. You do nothing extra.',
    technical:
      'Hooks in Claude Code and Cursor append events to a local spool; a launchd daemon ingests them into SQLite every minute, polls git every 5 minutes and Jira/Bitbucket (read-only scoped tokens) every 30. Everything is redacted on the way in.',
  },
  {
    screen: 'evidence',
    time: 'Evening',
    title: 'Facts first, claims second',
    simple:
      "Not everything counts the same. A commit or a passing test is a fact. An AI saying 'done' is only a claim. Personal side projects you hide never reach your digest.",
    technical:
      'Each item becomes cited evidence with a strength: commits (C), tests (T), branches (B), Jira (J) and PRs (R) are facts; agent sessions (S) are claims. Hidden sessions and My-rules filters are applied in code before anything is drafted.',
  },
  {
    screen: 'draft',
    time: '8 pm',
    title: 'A draft in your own format',
    simple:
      "At 8 pm your draft is ready, laid out the way you always write it, with your sections, your greeting and your manager's name. Anything the evidence can't back up is highlighted for you to check.",
    technical:
      'Your template is learned from past digests at setup. Code writes the header, Testing/Commits, PRs and the closing; the LLM (your own Cursor or Claude subscription) writes the narrative as JSON citing evidence ids; every statement is grounded, and invented numbers or tickets are flagged.',
  },
  {
    screen: 'review',
    time: '8:05 pm',
    title: 'Review in a couple of minutes',
    simple:
      "Your phone shows what to confirm and what your manager asked for. Tick asks Done (with proof) or Disagree (with a reason). Both show up in the digest's closing.",
    technical:
      "Compliance checks run your manager's standing rules against the text; gaps list missing sections, unverified lines and asks needing a real action. Ask state syncs live through Supabase realtime with row-level security.",
  },
  {
    screen: 'share',
    time: '8:10 pm',
    title: 'You send it, always',
    simple: 'Copy the digest and paste it into WhatsApp yourself. WorkDigest never messages anyone on your behalf.',
    technical:
      'There is no send API by design: the app copies plain WhatsApp-safe text (no Markdown) to the clipboard or the share sheet, and marks the day sent only when you say so.',
  },
];

@Component({
  selector: 'wd-story',
  imports: [Phone, ModeSwitch],
  template: `
    <section class="section story" id="your-evening" aria-labelledby="story-title">
      <header class="section-head">
        <p class="eyebrow">Your evening</p>
        <h2 id="story-title">From a busy day to a sent digest in five minutes.</h2>
        <wd-mode-switch />
      </header>
      <div class="story-grid">
        <div class="story-sticky" aria-hidden="true">
          <wd-phone [screen]="steps[active()].screen" />
          <div class="story-dots">
            @for (step of steps; track step.screen; let i = $index) {
              <span [class.on]="i === active()"></span>
            }
          </div>
        </div>
        <ol class="story-steps">
          @for (step of steps; track step.screen; let i = $index) {
            <li #stepEl class="story-step" [class.on]="i === active()" [attr.data-index]="i">
              <p class="eyebrow">{{ step.time }}</p>
              <h3>{{ step.title }}</h3>
              <p>{{ reading.mode() === 'simple' ? step.simple : step.technical }}</p>
              <div class="story-inline-phone"><wd-phone [screen]="step.screen" /></div>
            </li>
          }
        </ol>
      </div>
    </section>
  `,
})
export class Story implements AfterViewInit, OnDestroy {
  protected readonly reading = inject(ReadingMode);
  protected readonly steps = STEPS;
  protected readonly active = signal(0);
  private readonly elements = viewChildren<ElementRef<HTMLElement>>('stepEl');
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  private observer?: IntersectionObserver;

  ngAfterViewInit(): void {
    if (!this.browser || !('IntersectionObserver' in window)) return;
    // The step crossing the middle of the screen drives the phone.
    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) this.active.set(Number((entry.target as HTMLElement).dataset['index']));
        }
      },
      { rootMargin: '-45% 0px -45% 0px' },
    );
    for (const el of this.elements()) this.observer.observe(el.nativeElement);
  }

  ngOnDestroy(): void {
    this.observer?.disconnect();
  }
}
