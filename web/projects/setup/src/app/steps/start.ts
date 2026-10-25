import { Component, OnDestroy, inject, signal } from '@angular/core';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

export const POLL_MS = 3000;
export const POLL_LIMIT = 40;

@Component({
  selector: 'wd-start',
  template: `
    <h2>Start WorkDigest</h2>
    <p class="muted">WorkDigest runs quietly in the background and starts again after every login. It collects the
      day's evidence, keeps your phone in sync and drafts your digest before the time you send it.</p>
    <div class="glass card">
      <ol class="progress">
        <li [class.done]="started()">Start the background service</li>
        <li [class.done]="online()">This Mac says hello to your account</li>
      </ol>
      @if (waiting()) { <p role="status"><span class="spinner" aria-hidden="true"></span> Waiting for the first heartbeat…</p> }
      @if (online()) { <div class="note ok" role="status">Connected. Your phone will show "Mac connected".</div> }
      @if (slow()) {
        <div class="note warn">No heartbeat yet. Check your internet connection, then press <em>Try again</em>.
          Logs are in <code>~/Library/Application Support/WorkDigest/logs</code>.</div>
      }
      @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
      <div class="btn-row">
        <button class="btn btn-primary" type="button" [disabled]="action.busy() || waiting()" (click)="start()">
          {{ started() ? 'Try again' : 'Start WorkDigest' }}
        </button>
      </div>
    </div>
    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      <button class="btn btn-primary" type="button" [disabled]="!online()" (click)="wizard.next()">Continue</button>
    </div>
  `,
})
export class StartStep implements OnDestroy {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly started = signal(false);
  protected readonly online = signal(false);
  protected readonly waiting = signal(false);
  protected readonly slow = signal(false);
  private timer: ReturnType<typeof setTimeout> | undefined;

  ngOnDestroy(): void {
    clearTimeout(this.timer);
  }

  protected async start(): Promise<void> {
    this.slow.set(false);
    if (!(await this.action.run(() => this.api.post('start')))) return;
    this.started.set(true);
    this.waiting.set(true);
    this.poll(0);
  }

  private poll(attempt: number): void {
    this.timer = setTimeout(async () => {
      const beat = await this.api.get<{ online: boolean }>('heartbeat').catch(() => ({ online: false }));
      if (beat.online) {
        this.online.set(true);
        this.waiting.set(false);
      } else if (attempt + 1 >= POLL_LIMIT) {
        this.waiting.set(false);
        this.slow.set(true);
      } else {
        this.poll(attempt + 1);
      }
    }, POLL_MS);
  }
}
