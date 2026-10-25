import { Component, inject } from '@angular/core';
import { Phone } from '../phone';
import { Releases } from '../shared';

@Component({
  selector: 'wd-hero',
  imports: [Phone],
  template: `
    <section class="hero-section" aria-labelledby="hero-title">
      <div class="hero-copy">
        <p class="eyebrow rise">For engineers who send a daily digest</p>
        <h1 id="hero-title" class="rise rise-2">Your daily digest,<br /><span class="gradient-text">drafted from evidence.</span></h1>
        <p class="lead rise rise-3">
          WorkDigest quietly notes what you did on your Mac today, from AI agent sessions and commits to tests, Jira
          and pull requests. Before the time you send it, it drafts your digest in your manager's format. You
          review it on your phone and send it yourself.
        </p>
        <div class="btn-row rise rise-4">
          <a class="btn btn-primary" href="#get-started">Get started</a>
          <a class="btn" href="#how-it-works">How it works</a>
        </div>
        <p class="small muted rise rise-4">
          Android app{{ releases.latest()?.version ? ' · version ' + releases.latest()?.version : '' }} · Mac recorder for macOS ·
          Free for the team
        </p>
      </div>
      <div class="hero-phone rise rise-3">
        <div class="phone-glow" aria-hidden="true"></div>
        <wd-phone screen="today" [eager]="true" />
      </div>
    </section>
  `,
})
export class Hero {
  protected readonly releases = inject(Releases);
}
