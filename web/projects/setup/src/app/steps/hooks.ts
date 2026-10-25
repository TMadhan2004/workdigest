import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

@Component({
  selector: 'wd-hooks',
  imports: [FormsModule],
  template: `
    <h2>Agent capture</h2>
    <p class="muted">Small hooks in Claude Code and Cursor note what each agent session did: your prompts, the
      commands run and the tests passed. This is the evidence your digest is drafted from.</p>
    <div class="glass card">
      <h3>What is captured, and what is not</h3>
      <ul>
        <li>Prompts and agent replies are cut short and <strong>redacted</strong>: tokens, keys, passwords and emails are removed.</li>
        <li>Files are recorded by name, never by content. <code>.env</code> files and keys are skipped.</li>
        <li>Each settings file is backed up before it changes. <code>digest uninstall</code> removes only these hooks.</li>
        <li>Sessions in folders you exclude, or that you hide in the app, never reach a digest.</li>
      </ul>
      <label class="check"><input type="checkbox" name="claude" [(ngModel)]="claude" />
        <span><strong>Claude Code</strong> <span class="muted small">(~/.claude/settings.json)</span>
          @if (wizard.state()?.hooks?.claude) { <span class="chip ok">installed</span> }</span></label>
      <label class="check"><input type="checkbox" name="cursor" [(ngModel)]="cursor" />
        <span><strong>Cursor</strong> <span class="muted small">(~/.cursor/hooks.json)</span>
          @if (wizard.state()?.hooks?.cursor) { <span class="chip ok">installed</span> }</span></label>
    </div>
    @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      <button class="btn btn-primary" type="button" [disabled]="action.busy()" (click)="save()">
        {{ claude || cursor ? 'Install and continue' : 'Continue without capture' }}
      </button>
    </div>
  `,
})
export class HooksStep implements OnInit {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected claude = true;
  protected cursor = true;

  ngOnInit(): void {
    const ai = this.wizard.state()?.ai.provider;
    if (ai === 'claude_cli') this.cursor = !!this.wizard.state()?.hooks.cursor;
  }

  protected async save(): Promise<void> {
    if (!this.claude && !this.cursor) return this.wizard.next();
    if (await this.action.run(() => this.api.post('hooks', { claude: this.claude, cursor: this.cursor }))) {
      await this.wizard.next();
    }
  }
}
