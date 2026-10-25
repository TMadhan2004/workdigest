import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

@Component({
  selector: 'wd-repos',
  imports: [FormsModule],
  template: `
    <h2>Your repositories</h2>
    <p class="muted">WorkDigest reads commits and branches (never your code) from the repositories you tick.
      Leave out personal projects and tools.</p>
    <form class="glass card" (ngSubmit)="scan()">
      <div class="field">
        <label for="root">Where are your projects?</label>
        <div class="btn-row nowrap">
          <input id="root" name="root" type="text" [(ngModel)]="root" placeholder="~/Desktop" />
          <button class="btn" type="submit" [disabled]="scanning.busy()">
            @if (scanning.busy()) { <span class="spinner" aria-hidden="true"></span> } Find repositories
          </button>
        </div>
        <span class="hint">A folder that contains your git repositories, for example <code>~/Desktop</code> or <code>~/code</code>.</span>
      </div>
      @if (scanning.error()) { <div class="note error" role="alert">{{ scanning.error() }}</div> }
    </form>

    @if (found().length) {
      <fieldset class="glass card list rise">
        <legend class="label">{{ found().length }} repositories found · {{ chosen().size }} ticked</legend>
        <div class="btn-row">
          <button class="btn btn-ghost small" type="button" (click)="all(true)">Tick all</button>
          <button class="btn btn-ghost small" type="button" (click)="all(false)">Untick all</button>
        </div>
        @for (path of found(); track path) {
          <label class="check">
            <input type="checkbox" [checked]="chosen().has(path)" (change)="toggle(path)" />
            <span><strong>{{ base(path) }}</strong><br /><span class="muted small">{{ path }}</span></span>
          </label>
        }
      </fieldset>
    }

    @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      <button class="btn btn-primary" type="button" [disabled]="!chosen().size || action.busy()" (click)="save()">
        Use {{ chosen().size }} {{ chosen().size === 1 ? 'repository' : 'repositories' }}
      </button>
    </div>
  `,
})
export class ReposStep implements OnInit {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly scanning = new Action();
  protected readonly found = signal<string[]>([]);
  protected readonly chosen = signal<Set<string>>(new Set());
  protected root = '~/Desktop';

  ngOnInit(): void {
    const saved = this.wizard.state()?.repos ?? [];
    if (saved.length) {
      this.found.set(saved);
      this.chosen.set(new Set(saved));
    }
  }

  protected base(path: string): string {
    return path.split('/').filter(Boolean).pop() ?? path;
  }

  protected async scan(): Promise<void> {
    const result = await this.scanning.run(() => this.api.post<{ repos: string[] }>('repos/scan', { root: this.root }));
    if (!result) return;
    const merged = [...new Set([...this.chosen(), ...result.repos])];
    this.found.set(merged);
  }

  protected toggle(path: string): void {
    const next = new Set(this.chosen());
    if (next.has(path)) next.delete(path);
    else next.add(path);
    this.chosen.set(next);
  }

  protected all(on: boolean): void {
    this.chosen.set(on ? new Set(this.found()) : new Set());
  }

  protected async save(): Promise<void> {
    if (await this.action.run(() => this.api.post('repos', { paths: [...this.chosen()] }))) await this.wizard.next();
  }
}
