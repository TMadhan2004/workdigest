import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

@Component({
  selector: 'wd-account',
  imports: [FormsModule],
  template: `
    @let state = wizard.state();
    <h2>Your account</h2>
    <p class="muted">
      One account for this Mac and your phone. Use your <strong>@{{ state?.team?.domain }}</strong> email;
      other addresses are refused.
    </p>

    @if (state?.account?.signed_in && !changing()) {
      <div class="note ok" role="status">Signed in as {{ state?.account?.email }}.</div>
      <div class="btn-row actions">
        <button class="btn btn-ghost" type="button" (click)="changing.set(true)">Use another account</button>
        <button class="btn btn-primary" type="button" (click)="wizard.next()">Continue</button>
      </div>
    } @else if (waiting()) {
      <div class="glass card">
        <h3>Check your inbox</h3>
        <p>We sent a confirmation link to <strong>{{ email }}</strong>. Open it, then come back here.</p>
        <p class="muted small">Not there after a minute? Look in spam, or go back and check the address.</p>
        @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
        <div class="btn-row">
          <button class="btn btn-ghost" type="button" (click)="waiting.set(false)">Back</button>
          <button class="btn btn-primary" type="button" [disabled]="action.busy()" (click)="confirmed()">
            @if (action.busy()) { <span class="spinner" aria-hidden="true"></span> } I've confirmed my email
          </button>
        </div>
      </div>
    } @else {
      <form class="glass card" (ngSubmit)="submit()">
        <div class="segmented" role="radiogroup" aria-label="Account">
          <label [class.on]="mode === 'signup'"><input type="radio" name="mode" value="signup" [(ngModel)]="mode" /> I'm new here</label>
          <label [class.on]="mode === 'signin'"><input type="radio" name="mode" value="signin" [(ngModel)]="mode" /> I already have an account</label>
        </div>
        <div class="field">
          <label for="email">Company email</label>
          <input id="email" name="email" type="email" autocomplete="email" required [(ngModel)]="email"
                 [placeholder]="'you@' + (state?.team?.domain ?? '')" />
        </div>
        <div class="field">
          <label for="password">{{ mode === 'signup' ? 'Choose a password' : 'Password' }}</label>
          <input id="password" name="password" type="password" required minlength="8" [(ngModel)]="password"
                 [autocomplete]="mode === 'signup' ? 'new-password' : 'current-password'" />
          @if (mode === 'signup') { <span class="hint">At least 8 characters. You'll use it to sign in on your phone.</span> }
        </div>
        @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
        <div class="btn-row">
          <button class="btn btn-primary" type="submit" [disabled]="action.busy() || !email || !password">
            @if (action.busy()) { <span class="spinner" aria-hidden="true"></span> }
            {{ mode === 'signup' ? 'Create account' : 'Sign in' }}
          </button>
        </div>
      </form>
    }
  `,
})
export class AccountStep {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly waiting = signal(false);
  protected readonly changing = signal(false);
  protected mode: 'signup' | 'signin' = 'signup';
  protected email = '';
  protected password = '';

  protected async submit(): Promise<void> {
    const result = await this.action.run(() =>
      this.api.post<{ status: string }>('account', { mode: this.mode, email: this.email, password: this.password }),
    );
    if (!result) return;
    if (result.status === 'confirm_email') {
      this.waiting.set(true);
      return;
    }
    this.password = '';
    await this.wizard.next();
  }

  protected async confirmed(): Promise<void> {
    if (await this.action.run(() => this.api.post('account/confirmed'))) {
      this.password = '';
      await this.wizard.next();
    }
  }
}
