import { Component, computed, input } from '@angular/core';

/** Screens of the real app, captured on an Android emulator with the fictional demo data. */
export type Screen = 'today' | 'timeline' | 'draft' | 'asks' | 'history' | 'settings' | 'signin';

const DESCRIPTIONS: Record<Screen, string> = {
  today: "The app's Today screen: the Mac's status, today's draft with the things to confirm, and the send-by countdown.",
  timeline: "The Timeline: the day's agent sessions, commits, tests and open pull requests.",
  draft: 'The draft digest in your format, with lines that need confirming highlighted.',
  asks: "Your manager's asks, each closed as Done with evidence or Disagree with a reason.",
  history: 'History: a month calendar of every digest, sent, edited or draft.',
  settings: 'Settings: your send-by time, weekday reminders, the AI on your Mac and your digest template.',
  signin: 'Signing in with the account created during the Mac setup.',
};

/** A phone showing a real screen of the app. */
@Component({
  selector: 'wd-phone',
  template: `
    <div class="phone">
      <div class="phone-screen">
        <span class="phone-camera" aria-hidden="true"></span>
        <div class="phone-status" aria-hidden="true">
          <span>18:00</span>
          <span class="phone-status-icons">
            <svg viewBox="0 0 16 12"><path d="M1 11h2V8H1zm4 0h2V6H5zm4 0h2V3H9zm4 0h2V0h-2z" /></svg>
            <svg viewBox="0 0 16 12"><path d="M8 11.5 15.5 3A11.5 11.5 0 0 0 .5 3z" /></svg>
            <svg viewBox="0 0 10 16"><path d="M3 0h4v1.5h2.5V16H.5V1.5H3z" /></svg>
          </span>
        </div>
        <img [src]="src()" [alt]="description()" width="412" height="915" decoding="async"
             [attr.loading]="eager() ? 'eager' : 'lazy'" [attr.fetchpriority]="eager() ? 'high' : null" />
      </div>
    </div>
  `,
})
export class Phone {
  readonly screen = input<Screen>('today');
  /** The first phone on the page loads at once; the others when they come near. */
  readonly eager = input(false);
  protected readonly src = computed(() => `/screens/${this.screen()}.webp`);
  protected readonly description = computed(() => DESCRIPTIONS[this.screen()]);
}
