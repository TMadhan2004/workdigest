import { Component, OnInit, inject, signal } from '@angular/core';
import { Api, AiState } from '../api';
import { Action, Wizard } from '../wizard';

const STATE_TEXT: Record<AiState['state'], string> = {
  ok: 'Works',
  missing: 'Not installed',
  logged_out: 'Signed out',
  failing: 'Not working',
};

@Component({
  selector: 'wd-ai',
  template: `
    <h2>AI for drafts</h2>
    <p class="muted">The draft's wording is written by the AI you already use, with your own subscription. Facts
      (commits, tests, PRs) are always written by WorkDigest itself. We try each one with a tiny prompt.</p>

    @if (checking.busy()) {
      <p role="status"><span class="spinner" aria-hidden="true"></span> Trying Cursor and Claude Code… this can take up to a minute.</p>
    }
    @if (checking.error()) { <div class="note error" role="alert">{{ checking.error() }}</div> }

    <div class="choices" role="radiogroup" aria-label="AI for drafts">
      @for (entry of entries(); track entry.key) {
        <label class="glass card choice" [class.on]="choice() === entry.key" [class.locked]="entry.value.state !== 'ok'">
          <input type="radio" name="ai" [value]="entry.key" [checked]="choice() === entry.key"
                 [disabled]="entry.value.state !== 'ok'" (change)="choice.set(entry.key)" />
          <span class="grow">
            <strong>{{ entry.value.label }}</strong>
            <span class="chip" [class.ok]="entry.value.state === 'ok'" [class.warn]="entry.value.state !== 'ok'">
              {{ stateText(entry.value.state) }}</span>
            @if (entry.value.state !== 'ok') {
              <span class="block muted small">{{ entry.value.detail || 'It did not answer.' }}</span>
              @if (entry.value.state === 'logged_out' || entry.value.state === 'failing') {
                <span class="block small">Open Terminal, run <code>{{ entry.value.login }}</code>, then press <em>Check again</em>.</span>
              }
              @if (entry.value.state === 'missing') {
                <span class="block small">Not on this Mac. That's fine if you use the other one.</span>
              }
            }
          </span>
        </label>
      }
      <label class="glass card choice" [class.on]="choice() === 'none'">
        <input type="radio" name="ai" value="none" [checked]="choice() === 'none'" (change)="choice.set('none')" />
        <span class="grow"><strong>Facts only</strong>
          <span class="block muted small">No AI: you write the wording yourself. You can switch later in the phone app.</span></span>
      </label>
    </div>
    <p class="muted small">If a subscription ends or a login expires later, drafts become facts only and you get a
      notification on this Mac and on your phone.</p>

    @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      <button class="btn" type="button" [disabled]="checking.busy()" (click)="check()">Check again</button>
      <button class="btn btn-primary" type="button" [disabled]="!choice() || action.busy()" (click)="save()">Continue</button>
    </div>
  `,
})
export class AiStep implements OnInit {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly checking = new Action();
  protected readonly action = new Action();
  protected readonly results = signal<Record<string, AiState>>({});
  protected readonly choice = signal('');

  ngOnInit(): void {
    void this.check();
  }

  protected entries(): { key: string; value: AiState }[] {
    return Object.entries(this.results()).map(([key, value]) => ({ key, value }));
  }

  protected stateText(state: AiState['state']): string {
    return STATE_TEXT[state];
  }

  protected async check(): Promise<void> {
    const result = await this.checking.run(() => this.api.post<Record<string, AiState>>('ai/check'));
    if (!result) return;
    this.results.set(result);
    const saved = this.wizard.state()?.ai.provider ?? '';
    const working = Object.keys(result).filter((k) => result[k].state === 'ok');
    if (saved === 'none' || working.includes(saved)) this.choice.set(saved);
    else this.choice.set(working[0] ?? 'none');
  }

  protected async save(): Promise<void> {
    if (await this.action.run(() => this.api.post('ai', { provider: this.choice() }))) await this.wizard.next();
  }
}
