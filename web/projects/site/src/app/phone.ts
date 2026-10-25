import { Component, input } from '@angular/core';

export type Screen = 'today' | 'capture' | 'evidence' | 'draft' | 'review' | 'share';

/** A phone showing the app (drawn with HTML, so it stays sharp and themable). */
@Component({
  selector: 'wd-phone',
  template: `
    <div class="phone" role="img" [attr.aria-label]="describe()">
      <div class="phone-notch" aria-hidden="true"></div>
      <div class="phone-screen" aria-hidden="true">
        @switch (screen()) {
          @case ('today') {
            <div class="ps ps-today">
              <p class="ps-muted">Good evening</p>
              <p class="ps-title">Friday, 23 October</p>
              <span class="ps-pill"><i class="dot"></i> Mac connected</span>
              <div class="ps-hero">
                <p class="ps-eyebrow">Today's draft</p>
                <p class="ps-big">Ready to review</p>
                <p class="ps-hero-muted">2 things to confirm · send before 9 pm</p>
              </div>
              <div class="ps-stats">
                <div><b>4</b><span>sessions</span></div>
                <div><b>12</b><span>commits</span></div>
                <div><b>2</b><span>PRs</span></div>
              </div>
              <p class="ps-sub">Priya's asks</p>
              <div class="ps-row"><i class="ps-ico ask"></i><div><b>Add PR review times</b><span>Raised 2× · needs a real action</span></div></div>
              <div class="ps-row"><i class="ps-ico ask"></i><div><b>Name the tests you add</b><span>Open since Wed</span></div></div>
              <p class="ps-sub">Today so far</p>
              <div class="ps-row"><i class="ps-ico claude"></i><div><b>Billing move</b><span>Claude Code · 2 h 10 min</span></div></div>
            </div>
          }
          @case ('capture') {
            <div class="ps">
              <p class="ps-title">Timeline</p>
              <div class="ps-row"><i class="ps-ico claude"></i><div><b>Claude Code</b><span>Move billing profiles · 2 h</span></div></div>
              <div class="ps-row"><i class="ps-ico cursor"></i><div><b>Cursor</b><span>Portal UI fixes · 40 min</span></div></div>
              <div class="ps-row"><i class="ps-ico git"></i><div><b>3 commits</b><span>billing-service · feat/billing-move</span></div></div>
              <div class="ps-row"><i class="ps-ico test"></i><div><b>Tests 650 / 650</b><span>vitest · passed</span></div></div>
              <div class="ps-row"><i class="ps-ico pr"></i><div><b>PR #41 opened</b><span>Bitbucket · waiting for review</span></div></div>
              <div class="ps-row"><i class="ps-ico jira"></i><div><b>DEMO-101 → In Review</b><span>Jira · 6:40 pm</span></div></div>
              <div class="ps-row"><i class="ps-ico claude"></i><div><b>Claude Code</b><span>Write migration tests · 35 min</span></div></div>
              <div class="ps-row faded"><i class="ps-ico cursor"></i><div><b>Personal project</b><span>Hidden from digests</span></div></div>
            </div>
          }
          @case ('evidence') {
            <div class="ps">
              <p class="ps-title">Evidence</p>
              <div class="ps-ev"><span class="tag fact">Fact</span> Commit a41c9e: move credits to billing</div>
              <div class="ps-ev"><span class="tag fact">Fact</span> 650 / 650 tests passed</div>
              <div class="ps-ev"><span class="tag fact">Fact</span> DEMO-101 moved to In Review</div>
              <div class="ps-ev"><span class="tag claim">Claim</span> Agent: "migration verified on staging"</div>
              <div class="ps-ev"><span class="tag fact">Fact</span> PR #41 opened, review pending 3 h</div>
              <div class="ps-ev"><span class="tag fact">Fact</span> Branch feat/billing-move pushed</div>
              <div class="ps-ev"><span class="tag claim">Claim</span> Agent: "all edge cases handled"</div>
              <div class="ps-ev muted"><span class="tag hidden">Hidden</span> Personal side project</div>
              <p class="ps-muted small-note">Facts can be said as done. Claims need a fact or your confirmation.</p>
            </div>
          }
          @case ('draft') {
            <div class="ps ps-draft">
              <p class="ps-title">Draft</p>
              <p>Greetings Priya,</p>
              <p><b>Daily digest – Fri, 23 Oct</b></p>
              <p>1. Done today<br />&nbsp;&nbsp;DEMO-101 – Billing move<br />&nbsp;&nbsp;– 3 commits, 650/650 tests</p>
              <p class="ps-unverified">Verified on staging <em>confirm</em></p>
              <p>2. Blockers – None.</p>
              <p>3. Risks<br />&nbsp;&nbsp;Credit totals differ for 2 tenants – me, Mon 26 Oct</p>
              <p>4. PRs and review turnaround<br />&nbsp;&nbsp;#41 opened 3 pm, awaiting review</p>
              <p>5. Plan for upcoming days<br />&nbsp;&nbsp;Mon 26 Oct: staging rollout</p>
              <p class="ps-muted">Priya's asks: PR review times – done.</p>
            </div>
          }
          @case ('review') {
            <div class="ps">
              <p class="ps-title">Before you send</p>
              <div class="ps-gap"><b>Confirm</b> nothing was deployed today</div>
              <div class="ps-gap warn"><b>Unverified</b> "verified on staging"</div>
              <p class="ps-sub">Priya's asks</p>
              <div class="ps-ask"><span>Add PR review times</span><span class="btns"><em>Done</em><em>Disagree</em></span></div>
              <div class="ps-ask"><span>Name the tests you add</span><span class="btns"><em>Done</em><em>Disagree</em></span></div>
              <p class="ps-sub">Day</p>
              <div class="ps-gap"><b>Rate your day</b> 1 · 3 · 7 · 9</div>
              <div class="ps-gap"><b>Team</b> Rahul, Meera</div>
            </div>
          }
          @case ('share') {
            <div class="ps ps-share">
              <div class="ps-check">✓</div>
              <p class="ps-big">Copied</p>
              <p class="ps-muted">Paste it into WhatsApp yourself.<br />Nothing is ever sent for you.</p>
              <div class="ps-wa">Greetings Priya, Daily digest – Fri, 23 Oct…</div>
            </div>
          }
        }
      </div>
    </div>
  `,
})
export class Phone {
  readonly screen = input<Screen>('today');

  protected describe(): string {
    return {
      today: 'The app on a phone: today, the Mac connected, and the draft ready to review.',
      capture: 'The app timeline: agent sessions, commits, tests and a pull request from the day.',
      evidence: 'The evidence list: facts from commits, tests and Jira, a claim from an agent, a hidden personal session.',
      draft: "The draft digest for the manager, with one line highlighted to confirm.",
      review: "Before sending: things to confirm and the manager's asks with Done and Disagree.",
      share: 'Copied: the digest is pasted into WhatsApp by you.',
    }[this.screen()];
  }
}
