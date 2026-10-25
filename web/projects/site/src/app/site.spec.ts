import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { FEATURES } from './sections/features';
import { QUESTIONS } from './sections/faq';
import { STAGES } from './sections/how';
import { ROWS } from './sections/privacy';
import { qrDataUrl } from './sections/start';
import { STEPS } from './sections/story';
import { ModeSwitch, ReadingMode, installCommand } from './shared';

describe('website', () => {
  beforeEach(() => {
    localStorage.removeItem('wd-mode');
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
  });

  it('writes every explanation twice: plainly and technically', () => {
    for (const item of [...STEPS, ...FEATURES, ...STAGES]) {
      expect(item.simple.length).toBeGreaterThan(30);
      expect(item.technical.length).toBeGreaterThan(30);
      expect(item.simple).not.toEqual(item.technical);
    }
    expect(ROWS.every((r) => r.technical && r.stays && r.leaves)).toBeTrue();
    expect(QUESTIONS.length).toBeGreaterThanOrEqual(6);
  });

  it('remembers Simple / Technical and every switch follows it', async () => {
    const fixture = TestBed.createComponent(ModeSwitch);
    await fixture.whenStable();
    const technical = fixture.nativeElement.querySelectorAll('button')[1] as HTMLButtonElement;
    technical.click();
    await fixture.whenStable();
    expect(TestBed.inject(ReadingMode).mode()).toBe('technical');
    expect(technical.getAttribute('aria-checked')).toBe('true');
    expect(localStorage.getItem('wd-mode')).toBe('technical');
  });

  it('builds the install command from the address the page was opened on', () => {
    expect(installCommand()).toBe(`curl -fsSL ${location.origin}/install.sh | bash`);
    expect(qrDataUrl('https://x.example/download/android')).toMatch(/^data:image\/gif;base64,/);
  });
});
