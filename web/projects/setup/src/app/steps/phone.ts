import { Component, computed, inject, signal } from '@angular/core';
import qrcode from 'qrcode-generator';
import { Api } from '../api';
import { Action, Wizard } from '../wizard';

/** A QR code image (data URL) for ``text``. */
export function qrDataUrl(text: string): string {
  const code = qrcode(0, 'M');
  code.addData(text);
  code.make();
  return code.createDataURL(6, 2);
}

@Component({
  selector: 'wd-phone',
  template: `
    @let team = wizard.state()?.team;
    @if (!finished()) {
      <h2>Your phone</h2>
      <p class="muted">Install the WorkDigest app on your Android phone and sign in with
        <strong>{{ wizard.state()?.account?.email }}</strong>, the same account you just made.</p>
      <div class="glass card phone">
        @if (qr(); as src) {
          <img [src]="src" width="220" height="220" alt="QR code that opens the app download" />
        }
        <div>
          <ol>
            <li>Scan the code with your phone's camera, or open
              @if (team?.apk_url) { <a [href]="team?.apk_url">{{ team?.apk_url }}</a> } @else { the WorkDigest website }.</li>
            <li>Allow installing apps from your browser when Android asks.</li>
            <li>Open WorkDigest and sign in.</li>
          </ol>
          <p class="muted small">The app updates itself: when a new version is out, you'll get a notification.</p>
        </div>
      </div>
      @if (action.error()) { <div class="note error" role="alert">{{ action.error() }}</div> }
      <div class="btn-row actions">
        <button class="btn btn-ghost" type="button" (click)="wizard.back()">Back</button>
        <button class="btn btn-primary" type="button" (click)="finish()">Finish</button>
      </div>
    } @else {
      <section class="hero card rise">
        <h2 class="display">You're all set</h2>
        <p class="muted">WorkDigest is running. Your draft will be ready on your phone at
          {{ wizard.state()?.profile?.draft_at }}, and your phone reminds you before
          {{ wizard.state()?.profile?.send_by }} on weekdays. Review it, then copy it into WhatsApp yourself.
          You can close this tab.</p>
        <p class="muted small">To change anything later, run <code>digest setup</code> in Terminal.</p>
      </section>
    }
  `,
})
export class PhoneStep {
  protected readonly wizard = inject(Wizard);
  private readonly api = inject(Api);
  protected readonly action = new Action();
  protected readonly finished = signal(false);
  protected readonly qr = computed(() => {
    const url = this.wizard.state()?.team.apk_url;
    return url ? qrDataUrl(url) : '';
  });

  protected async finish(): Promise<void> {
    if (await this.action.run(() => this.api.post('finish'))) this.finished.set(true);
  }
}
