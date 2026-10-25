import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api, Check } from '../api';
import { Action, Wizard } from '../wizard';

interface Result {
  jira: Check;
  bitbucket: Check;
}

@Component({
  selector: 'wd-atlassian',
  imports: [FormsModule],
  template: `
    @let s = wizard.state()?.atlassian;
    <h2>Jira and Bitbucket</h2>
    <p class="muted">Read-only access, so the digest can list your tickets, PRs and how long reviews took.
      The tokens are stored only in this Mac's Keychain.</p>

    <ol class="glass card howto">
      <li>Open <a href="https://id.atlassian.com/manage-profile/security/api-tokens" target="_blank" rel="noopener">Atlassian API tokens</a>
        and choose <strong>Create API token with scopes</strong>.</li>
      <li><strong>Jira token:</strong> app <em>Jira</em>, scopes <code>read:jira-work</code> and <code>read:jira-user</code>.</li>
      <li><strong>Bitbucket token:</strong> app <em>Bitbucket</em>, scopes <code>read:repository:bitbucket</code>,
        <code>read:pullrequest:bitbucket</code> and <code>read:user:bitbucket</code>.</li>
      <li>Paste each token below. You can't see a token again after leaving Atlassian's page, so paste it right away.</li>
    </ol>

    <form class="glass card" (ngSubmit)="save()">
      <div class="field">
        <label for="aemail">Atlassian email</label>
        <input id="aemail" name="aemail" type="email" [(ngModel)]="email" autocomplete="email" />
      </div>
      <div class="field">
        <label for="jira">Jira token @if (s?.jira_token) { <span class="chip ok">saved</span> }</label>
        <input id="jira" name="jira" type="password" autocomplete="off" [(ngModel)]="jira"
               [placeholder]="s?.jira_token ? 'Leave empty to keep the saved token' : 'Paste the Jira token'" />
        <span class="hint">For {{ s?.jira_site }}</span>
      </div>
      <div class="field">
        <label for="bitbucket">Bitbucket token @if (s?.bitbucket_token) { <span class="chip ok">saved</span> }</label>
        <input id="bitbucket" name="bitbucket" type="password" autocomplete="off" [(ngModel)]="bitbucket"
               [placeholder]="s?.bitbucket_token ? 'Leave empty to keep the saved token' : 'Paste the Bitbucket token'" />
        <span class="hint">Workspace {{ s?.bitbucket_workspace }}</span>
      </div>
      @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
      @if (result(); as r) {
        <ul class="results" aria-live="polite">
          <li><span class="chip" [class.ok]="r.jira.ok" [class.error]="!r.jira.ok">Jira</span>
            {{ r.jira.ok ? 'Works, as ' + r.jira.detail : r.jira.detail }}</li>
          <li><span class="chip" [class.ok]="r.bitbucket.ok" [class.error]="!r.bitbucket.ok">Bitbucket</span>
            {{ r.bitbucket.ok ? 'Works, as ' + r.bitbucket.detail : r.bitbucket.detail }}</li>
        </ul>
      }
      <div class="btn-row">
        <button class="btn" type="submit" [disabled]="action.busy()">
          @if (action.busy()) { <span class="spinner" aria-hidden="true"></span> } Save and test
        </button>
      </div>
    </form>

    <div class="btn-row actions">
      <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
      <button class="btn btn-ghost" type="button" (click)="wizard.next()">Skip for now</button>
      <button class="btn btn-primary" type="button" [disabled]="!bothOk()" (click)="wizard.next()">Continue</button>
    </div>
  `,
})
export class AtlassianStep implements OnInit {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly result = signal<Result | null>(null);
  protected email = '';
  protected jira = '';
  protected bitbucket = '';

  ngOnInit(): void {
    this.email = this.wizard.state()?.atlassian.email ?? '';
  }

  protected bothOk(): boolean {
    const r = this.result();
    return !!r && r.jira.ok && r.bitbucket.ok;
  }

  protected async save(): Promise<void> {
    const result = await this.action.run(() =>
      this.api.post<Result>('atlassian', { email: this.email, jira_token: this.jira, bitbucket_token: this.bitbucket }),
    );
    this.jira = '';
    this.bitbucket = '';
    if (result) this.result.set(result);
  }
}
