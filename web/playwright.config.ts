import { defineConfig, devices } from '@playwright/test';

/** End-to-end tests for the setup wizard (mocked API) and the website, on a desktop and a Pixel-size phone. */
export default defineConfig({
  testDir: 'e2e',
  fullyParallel: true,
  forbidOnly: !!process.env['CI'],
  retries: process.env['CI'] ? 1 : 0,
  reporter: process.env['CI'] ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: { trace: 'retain-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'pixel', use: { ...devices['Pixel 7'], viewport: { width: 412, height: 915 } } },
  ],
  webServer: [
    {
      command: 'python3 -m http.server 4310 --bind 127.0.0.1 --directory ../recorder/src/workdigest/setup/web',
      url: 'http://127.0.0.1:4310/index.html',
      reuseExistingServer: !process.env['CI'],
    },
    {
      // The built website (`npm run build:site`), served as Vercel would serve the files.
      command: 'python3 -m http.server 4320 --bind 127.0.0.1 --directory dist/site/browser',
      url: 'http://127.0.0.1:4320/index.html',
      reuseExistingServer: !process.env['CI'],
    },
  ],
});
