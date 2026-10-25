import { Component, inject } from '@angular/core';
import { Wizard } from '../wizard';

@Component({
  selector: 'wd-welcome',
  template: `
    <section class="hero card rise">
      <h2 class="display">Set up WorkDigest</h2>
      <p class="muted">
        About ten minutes. Each step explains what it does and checks that it works. You can come
        back and change anything later by running <code>digest setup</code> again.
      </p>
    </section>
    <div class="grid rise rise-2">
      <article class="glass card">
        <h2 class="h3">What happens</h2>
        <ol>
          <li>Create your account with your company email.</li>
          <li>Tell us about you, your manager and how your digest usually looks.</li>
          <li>Pick your repositories and connect Jira and Bitbucket.</li>
          <li>Check that Cursor or Claude works on this Mac.</li>
          <li>Start the background service and get the phone app.</li>
        </ol>
      </article>
      <article class="glass card">
        <h2 class="h3">What stays on this Mac</h2>
        <p>Your Jira and Bitbucket tokens are stored only in the macOS Keychain. Raw prompts, code and
          tool output stay on this Mac.</p>
        <p class="muted">Only redacted summaries, drafts and your manager's asks are synced to your private
          account, so the phone app can show them. Nothing is ever sent to your manager automatically:
          you copy the digest yourself.</p>
      </article>
    </div>
    <div class="btn-row actions rise rise-3">
      <button class="btn btn-primary" type="button" (click)="wizard.next()">Get started</button>
    </div>
  `,
})
export class WelcomeStep {
  protected readonly wizard = inject(Wizard);
}
