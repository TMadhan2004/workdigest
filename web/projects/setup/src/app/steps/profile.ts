import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

export function splitList(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((part) => part.trim())
    .filter(Boolean);
}

@Component({
  selector: 'wd-profile',
  imports: [FormsModule],
  template: `
    <h2>You and your manager</h2>
    <p class="muted">Used to find your commits and to address the digest.</p>
    <form class="glass card" (ngSubmit)="save()">
      <div class="field">
        <label for="name">Your name</label>
        <input id="name" name="name" type="text" autocomplete="name" required [(ngModel)]="name" />
      </div>
      <div class="field">
        <label for="emails">Your git email(s)</label>
        <input id="emails" name="emails" type="text" required [(ngModel)]="emails" />
        <span class="hint">The address on your commits (from <code>git config user.email</code>). Separate several with commas.</span>
      </div>
      <div class="field">
        <label for="manager">Your manager's first name</label>
        <input id="manager" name="manager" type="text" required [(ngModel)]="manager" />
        <span class="hint">The digest opens with "Greetings {{ manager || 'Name' }},". The app shows "{{ manager || 'Name' }}'s rules".</span>
      </div>
      <div class="field">
        <label for="team">People you usually work with (optional)</label>
        <input id="team" name="team" type="text" [(ngModel)]="team" />
        <span class="hint">Shown as "Team members" in the digest header. You can change it each day.</span>
      </div>
      @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
      <div class="btn-row">
        <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
        <button class="btn btn-primary" type="submit" [disabled]="action.busy()">Save and continue</button>
      </div>
    </form>
  `,
})
export class ProfileStep implements OnInit {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected name = '';
  protected emails = '';
  protected manager = '';
  protected team = '';

  ngOnInit(): void {
    const profile = this.wizard.state()?.profile;
    if (!profile) return;
    this.name = profile.name;
    this.emails = profile.emails.join(', ');
    this.manager = profile.manager_name;
    this.team = profile.team_members.join(', ');
  }

  protected async save(): Promise<void> {
    const saved = await this.action.run(() =>
      this.api.post('profile', {
        name: this.name,
        emails: splitList(this.emails),
        manager_name: this.manager,
        team_members: splitList(this.team),
      }),
    );
    if (saved) await this.wizard.next();
  }
}
