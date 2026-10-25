import { Component, OnInit, inject } from '@angular/core';
import { Api } from './api';
import { AccountStep } from './steps/account';
import { AiStep } from './steps/ai';
import { AtlassianStep } from './steps/atlassian';
import { ConversationStep } from './steps/conversation';
import { HooksStep } from './steps/hooks';
import { PhoneStep } from './steps/phone';
import { ProfileStep } from './steps/profile';
import { ReposStep } from './steps/repos';
import { StartStep } from './steps/start';
import { WelcomeStep } from './steps/welcome';
import { STEPS, Wizard } from './wizard';

@Component({
  selector: 'wd-root',
  imports: [
    WelcomeStep, AccountStep, ProfileStep, ConversationStep, ReposStep,
    AtlassianStep, AiStep, HooksStep, StartStep, PhoneStep,
  ],
  template: `
    <div class="aurora-bg" aria-hidden="true"><span></span><span></span><span></span></div>
    <header class="top">
      <h1 class="sr-only">WorkDigest setup: {{ wizard.step().title }}</h1>
      <span class="brand"><span class="logo" aria-hidden="true"></span> WorkDigest <span class="muted">setup</span></span>
      <span class="muted small count">Step {{ wizard.index() + 1 }} of {{ steps.length }}</span>
    </header>
    <div class="progress-bar" role="progressbar" aria-label="Setup progress" aria-valuemin="1"
         [attr.aria-valuemax]="steps.length" [attr.aria-valuenow]="wizard.index() + 1">
      <span [style.width.%]="((wizard.index() + 1) / steps.length) * 100"></span>
    </div>

    <div class="layout">
      <nav class="rail" aria-label="Setup steps">
        <ol>
          @for (step of steps; track step.id; let i = $index) {
            <li>
              <button type="button" [class.current]="i === wizard.index()" [class.done]="i < wizard.index()"
                      [attr.aria-current]="i === wizard.index() ? 'step' : null"
                      [disabled]="i > wizard.index() && !wizard.state()?.account?.signed_in" (click)="wizard.go(i)">
                <span class="dot" aria-hidden="true">{{ i < wizard.index() ? '✓' : i + 1 }}</span> {{ step.title }}
              </button>
            </li>
          }
        </ol>
      </nav>

      <main class="content" id="main">
        @if (api.expired()) {
          <div class="note error" role="alert">This setup link has expired. Run <code>digest setup</code> in Terminal again.</div>
        }
        @if (wizard.loadError(); as error) { <div class="note error" role="alert">{{ error }}</div> }
        @if (wizard.state()) {
          @for (step of [wizard.step()]; track step.id) {
            <div class="step">
              @switch (step.id) {
                @case ('welcome') { <wd-welcome /> }
                @case ('account') { <wd-account /> }
                @case ('profile') { <wd-profile /> }
                @case ('conversation') { <wd-conversation /> }
                @case ('repos') { <wd-repos /> }
                @case ('atlassian') { <wd-atlassian /> }
                @case ('ai') { <wd-ai /> }
                @case ('hooks') { <wd-hooks /> }
                @case ('start') { <wd-start /> }
                @case ('phone') { <wd-phone /> }
              }
            </div>
          }
        } @else if (!wizard.loadError()) {
          <p role="status"><span class="spinner" aria-hidden="true"></span> Loading…</p>
        }
      </main>
    </div>
  `,
})
export class App implements OnInit {
  protected readonly wizard = inject(Wizard);
  protected readonly api = inject(Api);
  protected readonly steps = STEPS;

  ngOnInit(): void {
    void this.wizard.load();
  }
}
