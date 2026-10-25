import AxeBuilder from '@axe-core/playwright';
import { expect, Page, test } from '@playwright/test';

test.use({ baseURL: 'http://127.0.0.1:4320' });

async function settle(page: Page): Promise<void> {
  // Scroll through once so every reveal has played, then wait for one-off animations.
  const height = await page.evaluate(() => document.body.scrollHeight);
  for (let y = 0; y < height; y += 600) await page.evaluate((top) => window.scrollTo(0, top), y);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity),
  );
}

test('the page explains WorkDigest and is accessible', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Your daily digest');
  for (const name of ['From a busy day', 'Everything the digest needs', 'Your Mac does the work', 'Built so nothing leaves', 'Ten minutes, once', 'Good to know']) {
    await expect(page.getByRole('heading', { name: new RegExp(name) })).toBeAttached();
  }
  await settle(page);
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target} ${v.nodes[0]?.failureSummary}`)).toEqual([]);
});

test('the page never scrolls sideways', async ({ page }) => {
  await page.goto('/');
  await settle(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
});

test('Simple / Technical rewrites every explanation, and is remembered', async ({ page }) => {
  await page.goto('/');
  const features = page.locator('#features');
  await expect(features.getByText('Swipe a session away')).toBeVisible();
  await features.getByRole('radio', { name: 'Technical' }).click();
  await expect(features.getByText('Hidden session keys sync to the Mac')).toBeVisible();
  // Every switch on the page follows the same choice.
  await expect(page.locator('#how-it-works').getByRole('radio', { name: 'Technical' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.locator('#how-it-works').getByText('row-level security')).toBeVisible();
  await page.reload();
  await expect(page.locator('#features').getByText('Hidden session keys sync to the Mac')).toBeVisible();
});

test('get started has the Mac command for this site and the Android download', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/#get-started');
  const start = page.locator('#get-started');
  await expect(start.getByText('curl -fsSL http://127.0.0.1:4320/install.sh | bash')).toBeVisible();
  await start.getByRole('button', { name: /Copy/ }).click();
  await expect(start.getByRole('button', { name: /Copy/ })).toHaveText('Copied');
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('curl -fsSL http://127.0.0.1:4320/install.sh | bash');
  await expect(start.getByRole('link', { name: 'Download for Android' })).toHaveAttribute('href', '/download/android');
});

test('the installer and the Vercel settings are published with the site', async ({ request }) => {
  const script = await request.get('/install.sh');
  expect(script.ok()).toBeTruthy();
  const text = await script.text();
  expect(text).toContain('tool install --force');
  expect(text).not.toContain('__SITE_URL__');
  const vercel = await (await request.get('/vercel.json')).json();
  expect(vercel.redirects[0]).toMatchObject({ source: '/download/android', permanent: false });
  expect(vercel.redirects[0].destination).toMatch(/releases\/latest\/download\/WorkDigest\.apk$/);
});

test('the story phone follows the step you are reading', async ({ page, isMobile }) => {
  test.skip(!!isMobile, 'on phones each step shows its own picture');
  await page.goto('/');
  await page.locator('.story-step').nth(2).scrollIntoViewIfNeeded();
  await page.evaluate(() => {
    const step = document.querySelectorAll('.story-step')[2] as HTMLElement;
    window.scrollTo(0, step.getBoundingClientRect().top + window.scrollY - window.innerHeight / 2 + step.offsetHeight / 2);
  });
  await expect(page.locator('.story-sticky .phone')).toHaveAttribute('aria-label', /draft digest/);
});

test('with reduced motion the diagram is fully lit and nothing waits to appear', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto('/#how-it-works');
  await expect(page.locator('.flow-node.lit')).toHaveCount(6);
  await expect(page.locator('.feature').first()).toHaveCSS('opacity', '1');
});
