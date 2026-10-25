import { isPlatformBrowser } from '@angular/common';
import {
  AfterViewInit, Component, Directive, ElementRef, Injectable, OnDestroy, PLATFORM_ID, computed, inject, input, signal,
} from '@angular/core';

export type Mode = 'simple' | 'technical';

/** How explanations are written: plain words, or the engineering detail. One choice for the page. */
@Injectable({ providedIn: 'root' })
export class ReadingMode {
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  readonly mode = signal<Mode>('simple');

  constructor() {
    if (!this.browser) return;
    try {
      const saved = localStorage.getItem('wd-mode');
      if (saved === 'technical') this.mode.set('technical');
    } catch {
      // storage blocked: keep the default
    }
  }

  set(mode: Mode): void {
    this.mode.set(mode);
    try {
      if (this.browser) localStorage.setItem('wd-mode', mode);
    } catch {
      // storage blocked: the choice lasts for this visit
    }
  }
}

/** The Simple / Technical switch (every copy controls the same page-wide choice). */
@Component({
  selector: 'wd-mode-switch',
  template: `
    <div class="mode-switch" role="radiogroup" [attr.aria-label]="label()">
      <button type="button" role="radio" [attr.aria-checked]="reading.mode() === 'simple'" (click)="reading.set('simple')">Simple</button>
      <button type="button" role="radio" [attr.aria-checked]="reading.mode() === 'technical'" (click)="reading.set('technical')">Technical</button>
      <span class="thumb" [class.right]="reading.mode() === 'technical'" aria-hidden="true"></span>
    </div>
  `,
})
export class ModeSwitch {
  protected readonly reading = inject(ReadingMode);
  readonly label = input('How much detail');
}

/** Adds `in` when the element scrolls into view, once (CSS does the motion). */
@Directive({ selector: '[wdReveal]', host: { class: 'reveal' } })
export class Reveal implements AfterViewInit, OnDestroy {
  private readonly element = inject(ElementRef<HTMLElement>);
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  private observer?: IntersectionObserver;

  ngAfterViewInit(): void {
    const el = this.element.nativeElement as HTMLElement;
    if (!this.browser || !('IntersectionObserver' in window)) {
      el.classList.add('in');
      return;
    }
    this.observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            el.classList.add('in');
            this.observer?.disconnect();
          }
        }
      },
      { rootMargin: '0px 0px -10% 0px', threshold: 0.12 },
    );
    this.observer.observe(el);
  }

  ngOnDestroy(): void {
    this.observer?.disconnect();
  }
}

/** A command or link with a Copy button that says "Copied". */
@Component({
  selector: 'wd-copy',
  template: `
    <div class="codeblock">
      <code tabindex="0" [attr.aria-label]="text()">{{ text() }}</code>
      <button class="btn btn-small" type="button" (click)="copy()" [attr.aria-label]="'Copy: ' + text()">
        {{ copied() ? 'Copied' : 'Copy' }}
      </button>
      <span class="sr-only" aria-live="polite">{{ copied() ? 'Copied to the clipboard' : '' }}</span>
    </div>
  `,
})
export class CopyText {
  readonly text = input.required<string>();
  protected readonly copied = signal(false);

  protected async copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.text());
      this.copied.set(true);
      setTimeout(() => this.copied.set(false), 2000);
    } catch {
      this.copied.set(false);
    }
  }
}

export interface ReleaseInfo {
  version: string;
  notes: string;
  apkSize?: number;
  publishedAt?: string;
}

/** The latest release (from /version.json, written by the release workflow). */
@Injectable({ providedIn: 'root' })
export class Releases {
  private readonly browser = isPlatformBrowser(inject(PLATFORM_ID));
  readonly latest = signal<ReleaseInfo | null>(null);
  readonly apkMb = computed(() => {
    const size = this.latest()?.apkSize;
    return size ? `${(size / (1024 * 1024)).toFixed(0)} MB` : '';
  });

  constructor() {
    if (this.browser) void this.load();
  }

  private async load(): Promise<void> {
    try {
      const response = await fetch('/version.json', { cache: 'no-store' });
      if (!response.ok) return;
      const data = await response.json();
      this.latest.set({
        version: String(data.version ?? ''),
        notes: String(data.notes ?? ''),
        apkSize: typeof data.android?.size === 'number' ? data.android.size : undefined,
        publishedAt: data.published_at,
      });
    } catch {
      // no release yet (local preview): the page still works
    }
  }
}

/** The address used while prerendering; scripts/prepare-site.mjs swaps in the real one. */
export const SITE_PLACEHOLDER = 'https://workdigest.example';

/** The command a teammate pastes into Terminal on their Mac. */
export function installCommand(): string {
  const origin = typeof location === 'undefined' || location.protocol === 'file:' ? SITE_PLACEHOLDER : location.origin;
  return `curl -fsSL ${origin}/install.sh | bash`;
}
