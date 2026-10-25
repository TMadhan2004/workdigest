import { Component, inject, signal } from '@angular/core';
import { Api, Preview } from '../api';
import { Action, Wizard } from '../wizard';

const HEADER_LABELS: Record<string, string> = {
  project: 'Project',
  team: 'Team members',
  in_short: 'In short',
  rating: 'Day rating',
};

@Component({
  selector: 'wd-conversation',
  template: `
    <h2>Your digest format</h2>
    <p class="muted">
      Everyone writes their digest a little differently. Upload a text file of your past digests and your
      manager's replies; WorkDigest learns your sections and their order, your greeting, your manager's
      rules and the asks that are still open.
    </p>

    <details class="glass card format">
      <summary><strong>How should the file look?</strong></summary>
      <p class="small">Put <code>Me:</code> on its own line before each digest you sent, and <code>Reply:</code> before each
        reply. Copy them from WhatsApp in order, oldest first. A <code>.txt</code> or <code>.md</code> file works.</p>
      <pre class="small">Me:
Greetings Priya,

Daily digest – Fri, 23 Oct 2026
In short: …

1. Done today
   …
2. Blockers – None.

Reply:
Hi Arjun, nice work. Please add PR links…</pre>
    </details>

    <div class="glass card drop" [class.over]="over()" (dragover)="$event.preventDefault(); over.set(true)"
         (dragleave)="over.set(false)" (drop)="drop($event)">
      <p><strong>{{ fileName() || 'Drop the file here' }}</strong></p>
      <label class="btn">
        Choose a file
        <input class="sr-only" type="file" accept=".txt,.md,text/plain,text/markdown" (change)="pick($event)" />
      </label>
    </div>

    @if (action.busy()) { <p role="status"><span class="spinner" aria-hidden="true"></span> Reading…</p> }
    @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }

    @if (preview(); as p) {
      <article class="glass card rise" aria-live="polite">
        <h3>What we learned from {{ p.digests }} digests and {{ p.replies }} replies</h3>
        <dl class="facts">
          <dt>Manager</dt><dd>{{ p.manager_name || 'not found: you entered it on the previous step' }}</dd>
          <dt>Greeting</dt><dd><code>{{ greeting(p) }}</code></dd>
          <dt>Header lines</dt><dd>{{ header(p) || 'none' }}</dd>
          @if (p.signoff) { <dt>Sign-off</dt><dd><code>{{ p.signoff }}</code></dd> }
        </dl>
        <h3>Your sections, in order</h3>
        <ol class="sections">
          @for (s of p.sections; track s.key) {
            <li>{{ s.title }}
              @if (s.inline) { <span class="chip">one line</span> }
              @if (s.custom) { <span class="chip warn">your own section</span> }
            </li>
          }
        </ol>
        @for (note of p.notes; track note) { <div class="note warn">{{ note }}</div> }
        <p class="muted small">Your manager's rules and open asks are imported too; you'll see them in the phone app.</p>
      </article>
    }

    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      @if (wizard.state()?.conversation?.learned && !preview()) {
        <button class="btn" type="button" (click)="wizard.next()">Keep my current format</button>
      }
      <button class="btn btn-primary" type="button" [disabled]="!preview() || action.busy()" (click)="apply()">
        Use this format
      </button>
    </div>
  `,
})
export class ConversationStep {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly preview = signal<Preview | null>(null);
  protected readonly fileName = signal('');
  protected readonly over = signal(false);
  private text = '';

  protected greeting(p: Preview): string {
    return p.greeting.replace('{manager}', p.manager_name || this.wizard.state()?.profile.manager_name || 'Name');
  }

  protected header(p: Preview): string {
    return p.header_fields.map((f) => HEADER_LABELS[f] ?? f).join(', ');
  }

  protected pick(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (file) void this.read(file);
  }

  protected drop(event: DragEvent): void {
    event.preventDefault();
    this.over.set(false);
    const file = event.dataTransfer?.files?.[0];
    if (file) void this.read(file);
  }

  async read(file: File): Promise<void> {
    this.fileName.set(file.name);
    this.preview.set(null);
    this.text = await file.text();
    const result = await this.action.run(() => this.api.post<Preview>('conversation/preview', { text: this.text }));
    if (result) this.preview.set(result);
  }

  protected async apply(): Promise<void> {
    const manager = this.wizard.state()?.profile.manager_name ?? '';
    const done = await this.action.run(() =>
      this.api.post('conversation/apply', { text: this.text, manager_name: manager }),
    );
    if (done) await this.wizard.next();
  }
}
