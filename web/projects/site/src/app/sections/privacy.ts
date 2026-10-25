import { Component, inject } from '@angular/core';
import { ModeSwitch, ReadingMode, Reveal } from '../shared';

interface Row {
  what: string;
  stays: string;
  leaves: string;
  technical: string;
}

export const ROWS: Row[] = [
  {
    what: 'Your prompts and agent replies',
    stays: 'Kept on your Mac, shortened and redacted',
    leaves: 'Only a short redacted summary',
    technical: 'Prompt excerpts capped at 600 chars; agent text at 1,500; each switch can be turned off in Settings → transmit.',
  },
  {
    what: 'Your code',
    stays: 'Never read. Files are noted by name only',
    leaves: 'Nothing',
    technical: 'Tool outputs are tail-capped and redacted; file contents are never stored; sensitive paths are skipped entirely.',
  },
  {
    what: 'Jira and Bitbucket tokens',
    stays: 'macOS Keychain only',
    leaves: 'Never',
    technical: 'Read-only scoped API tokens; every Keychain value is also a redaction pattern, so a token can never appear in evidence.',
  },
  {
    what: 'Your draft and asks',
    stays: 'On your Mac',
    leaves: 'To your own account, so the phone can show them',
    technical: 'Supabase row-level security: each row carries user_id and only auth.uid() = user_id can read or write it.',
  },
];

@Component({
  selector: 'wd-privacy',
  imports: [ModeSwitch, Reveal],
  template: `
    <section class="section" id="privacy" aria-labelledby="privacy-title">
      <header class="section-head">
        <p class="eyebrow">Privacy</p>
        <h2 id="privacy-title">Built so nothing leaves without a reason.</h2>
        <wd-mode-switch />
      </header>
      <div class="privacy-grid">
        @for (row of rows; track row.what) {
          <article wdReveal class="glass card privacy-card">
            <h3>{{ row.what }}</h3>
            @if (reading.mode() === 'simple') {
              <dl>
                <dt>On your Mac</dt><dd>{{ row.stays }}</dd>
                <dt>Leaves the Mac</dt><dd>{{ row.leaves }}</dd>
              </dl>
            } @else {
              <p>{{ row.technical }}</p>
            }
          </article>
        }
      </div>
      <div wdReveal class="hero card promise">
        <h3>Nothing is ever sent for you.</h3>
        <p class="muted">There is no button that messages your manager. You copy the digest and send it yourself, every time.</p>
      </div>
    </section>
  `,
})
export class Privacy {
  protected readonly reading = inject(ReadingMode);
  protected readonly rows = ROWS;
}
