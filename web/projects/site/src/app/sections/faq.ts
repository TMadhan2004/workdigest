import { Component } from '@angular/core';

export const QUESTIONS: { q: string; a: string }[] = [
  {
    q: 'Does it send anything to my manager?',
    a: 'No. WorkDigest drafts; you copy and send. There is no feature that messages anyone.',
  },
  {
    q: 'Which AI does it use, and who pays?',
    a: 'The Cursor or Claude Code you already use on your Mac, with your own subscription. If neither works, drafts are facts only (commits, tests, tickets and PRs) and you write the wording.',
  },
  {
    q: 'What if my AI subscription runs out?',
    a: "WorkDigest checks every hour. When the AI you chose stops working, you're told on the Mac and the phone, drafts switch to facts only, and they switch back by themselves when it works again.",
  },
  {
    q: 'Can my manager or teammates see my data?',
    a: "No. Each account can only read its own rows; that's enforced by the database, not the app.",
  },
  {
    q: "I don't use one of the tools. Is that a problem?",
    a: 'No. Every source is optional. Without Jira or Bitbucket tokens, the digest asks you to add PR links yourself.',
  },
  {
    q: 'Does it work on iPhone or Windows?',
    a: "Not yet: the recorder needs macOS and the app is Android only.",
  },
  {
    q: 'How do I update or uninstall?',
    a: 'Your phone and Mac tell you when an update is out. On the Mac run `digest update`; to remove everything run `digest uninstall` (it asks before deleting your data).',
  },
  {
    q: 'My digest format is different from my colleague’s. Is that OK?',
    a: 'Yes. Setup learns your sections, greeting and sign-off from your own past digests, and your manager’s name from the greetings.',
  },
];

@Component({
  selector: 'wd-faq',
  template: `
    <section class="section" id="faq" aria-labelledby="faq-title">
      <header class="section-head">
        <p class="eyebrow">Questions</p>
        <h2 id="faq-title">Good to know</h2>
      </header>
      <div class="faq">
        @for (item of questions; track item.q) {
          <details class="glass faq-item">
            <summary>{{ item.q }}</summary>
            <p>{{ item.a }}</p>
          </details>
        }
      </div>
    </section>
  `,
})
export class Faq {
  protected readonly questions = QUESTIONS;
}
