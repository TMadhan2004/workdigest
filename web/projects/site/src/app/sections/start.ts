import { isPlatformBrowser } from '@angular/common';
import { Component, OnInit, PLATFORM_ID, inject, signal } from '@angular/core';
import qrcode from 'qrcode-generator';
import { CopyText, Releases, Reveal, installCommand } from '../shared';

/** A QR code image (data URL) for ``text``. */
export function qrDataUrl(text: string): string {
  const code = qrcode(0, 'M');
  code.addData(text);
  code.make();
  return code.createDataURL(5, 2);
}

@Component({
  selector: 'wd-start',
  imports: [CopyText, Reveal],
  template: `
    <section class="section" id="get-started" aria-labelledby="start-title">
      <header class="section-head">
        <p class="eyebrow">Get started</p>
        <h2 id="start-title">Ten minutes, once.</h2>
        <p class="lead">Set up your Mac first: that's where your account is created. Then sign in on your phone.</p>
      </header>
      <ol class="start-steps">
        <li wdReveal class="glass card start-step">
          <span class="stage-num" aria-hidden="true">1</span>
          <h3>On your Mac</h3>
          <p>Open <strong>Terminal</strong> (press ⌘ Space, type "Terminal"), paste this and press Return:</p>
          <wd-copy [text]="command()" />
          <p class="small muted">It installs WorkDigest in your home folder (no admin password) and opens a setup page in your
            browser. Have your <strong>@ei.study</strong> email, a few past digests with your manager's replies, and about ten
            minutes.</p>
        </li>
        <li wdReveal class="glass card start-step">
          <span class="stage-num" aria-hidden="true">2</span>
          <h3>On your Android phone</h3>
          <div class="download">
            <div>
              <a class="btn btn-primary" href="/download/android">Download for Android</a>
              <p class="small muted">
                {{ releases.latest()?.version ? 'Version ' + releases.latest()?.version : 'Latest version' }}
                {{ releases.apkMb() ? '· ' + releases.apkMb() : '' }} · Android 8 or newer
              </p>
            </div>
            @if (qr()) {
              <figure class="qr">
                <img [src]="qr()" width="150" height="150" alt="QR code to download the Android app" />
                <figcaption class="small muted">Scan with your phone</figcaption>
              </figure>
            }
          </div>
          <p class="small muted">Android asks you to allow installs from your browser the first time. The app updates itself
            after that.</p>
        </li>
        <li wdReveal class="glass card start-step">
          <span class="stage-num" aria-hidden="true">3</span>
          <h3>Sign in, and that's it</h3>
          <p>Use the same email and password as on your Mac. Around 8 pm your first draft is ready.</p>
        </li>
      </ol>
    </section>
  `,
})
export class Start implements OnInit {
  protected readonly releases = inject(Releases);
  protected readonly command = signal(installCommand());
  protected readonly qr = signal('');
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));

  ngOnInit(): void {
    if (!this.browser) return;
    this.command.set(installCommand());
    this.qr.set(qrDataUrl(`${location.origin}/download/android`));
  }
}
