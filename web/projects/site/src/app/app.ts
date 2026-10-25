import { isPlatformBrowser } from '@angular/common';
import { Component, OnDestroy, OnInit, PLATFORM_ID, inject, signal } from '@angular/core';
import { RouterOutlet } from '@angular/router';
import { ModeSwitch, Releases } from './shared';

@Component({
  selector: 'wd-root',
  imports: [RouterOutlet, ModeSwitch],
  template: `
    <a class="skip" href="#main">Skip to content</a>
    <div class="aurora-bg" aria-hidden="true"><span></span><span></span><span></span></div>
    <header class="nav" [class.scrolled]="scrolled()">
      <nav class="nav-inner" aria-label="Main">
        <a class="brand" href="/"><span class="logo" aria-hidden="true"></span> WorkDigest</a>
        <ul class="nav-links">
          <li><a href="#your-evening">Your evening</a></li>
          <li><a href="#features">Features</a></li>
          <li><a href="#how-it-works">How it works</a></li>
          <li><a href="#privacy">Privacy</a></li>
          <li><a href="#faq">FAQ</a></li>
        </ul>
        <div class="nav-end">
          <wd-mode-switch label="Explanations" />
          <a class="btn btn-primary btn-small" href="#get-started">Get started</a>
        </div>
      </nav>
    </header>
    <main id="main">
      <router-outlet />
    </main>
    <footer class="footer">
      <div class="footer-inner">
        <p><span class="brand"><span class="logo" aria-hidden="true"></span> WorkDigest</span></p>
        <p class="small muted">
          {{ releases.latest()?.version ? 'Version ' + releases.latest()?.version + ' · ' : '' }}
          <a href="/releases">Release notes</a> · <a href="#privacy">Privacy</a> · <a href="#faq">Help</a>
        </p>
        <p class="small muted">Your daily digest, drafted from evidence. Nothing is ever sent without you.</p>
      </div>
    </footer>
  `,
})
export class App implements OnInit, OnDestroy {
  protected readonly releases = inject(Releases);
  protected readonly scrolled = signal(false);
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  private readonly onScroll = () => this.scrolled.set(window.scrollY > 8);

  ngOnInit(): void {
    if (this.browser) window.addEventListener('scroll', this.onScroll, { passive: true });
  }

  ngOnDestroy(): void {
    if (this.browser) window.removeEventListener('scroll', this.onScroll);
  }
}
