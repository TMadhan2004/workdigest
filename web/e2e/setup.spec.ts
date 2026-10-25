import AxeBuilder from '@axe-core/playwright';
import { expect, Page, test } from '@playwright/test';

test.use({ baseURL: 'http://127.0.0.1:4310' });

const STATE = {
  team: { domain: 'ei.study', site_url: 'https://workdigest.example', apk_url: 'https://workdigest.example/download/android' },
  account: { email: '', signed_in: false },
  profile: { name: 'Arjun K', emails: ['arjun.k@ei.study'], manager_name: '', team_members: [], send_by: '19:00', draft_at: '17:00' },
  conversation: { learned: false },
  repos: [],
  atlassian: { email: 'arjun.k@ei.study', jira_token: false, bitbucket_token: false, jira_site: 'https://ei-study.atlassian.net', bitbucket_workspace: 'ei-india-admin' },
  ai: { provider: 'cursor_cli' },
  hooks: { claude: false, cursor: false },
  service: { installed: false, running: false },
};

const PREVIEW = {
  manager_name: 'Sana', greeting: 'Hi {manager},', header_fields: ['project', 'in_short'], signoff: 'Regards,\nArjun',
  sections: [
    { key: 'done_today', title: 'Done today', inline: false, custom: false },
    { key: 'custom_customer_impact', title: 'Customer impact', inline: false, custom: true },
    { key: 'blockers', title: 'Blockers', inline: true, custom: false },
  ],
  digests: 2, replies: 1, notes: [],
};

/** Mocks the local setup server; records what the page sent. */
async function mockApi(page: Page): Promise<Record<string, unknown>> {
  const sent: Record<string, unknown> = {};
  const state = structuredClone(STATE) as typeof STATE;
  let confirmed = false;
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace('/api/', '');
    expect(request.headers()['x-setup-token']).toBe('t0k3n');
    const body = request.postDataJSON?.() ?? null;
    sent[path] = body;
    const reply = (data: unknown, status = 200) => route.fulfill({ status, json: data });
    switch (path) {
      case 'state': return reply(state);
      case 'account':
        if (!String(body.email).endsWith('@ei.study')) return reply({ error: 'use your @ei.study email address' }, 422);
        state.account = { email: body.email, signed_in: false };
        return reply({ status: 'confirm_email', email: body.email });
      case 'account/confirmed':
        if (!confirmed) { confirmed = true; return reply({ error: 'not confirmed yet: open the link in the email from Supabase, then try again' }, 422); }
        state.account.signed_in = true;
        return reply({ status: 'signed_in' });
      case 'profile': state.profile = { ...state.profile, manager_name: body.manager_name }; return reply({ saved: true });
      case 'conversation/preview': return reply(PREVIEW);
      case 'conversation/apply': return reply({ digests: 2, replies: 1, asks: 1, rules: 1 });
      case 'repos/scan': return reply({ repos: ['/Users/a/Desktop/billing-service', '/Users/a/Desktop/portal-ui'] });
      case 'repos': return reply({ saved: body.paths.length });
      case 'atlassian': return reply({ jira: { ok: true, detail: 'Arjun K' }, bitbucket: { ok: true, detail: 'Arjun K' } });
      case 'ai/check': return reply({
        cursor_cli: { label: 'Cursor', state: 'ok', detail: '', login: 'cursor-agent login' },
        claude_cli: { label: 'Claude Code', state: 'logged_out', detail: 'signed out; log in again on the Mac', login: 'claude' },
      });
      case 'ai': return reply({ provider: body.provider });
      case 'hooks': return reply({ claude: true, cursor: true });
      case 'start': return reply({ started: true });
      case 'heartbeat': return reply({ online: true });
      case 'finish': return reply({ bye: true });
    }
    return reply({ error: 'unknown' }, 404);
  });
  return sent;
}

async function checkA11y(page: Page): Promise<void> {
  // Let entrance animations finish: half-faded text would be measured mid-fade.
  await page.waitForFunction(() =>
    document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getTiming().iterations === Infinity),
  );
  const results = await new AxeBuilder({ page }).disableRules(['region']).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target} ${v.nodes[0]?.failureSummary}`)).toEqual([]);
}

test('a new teammate goes through every step', async ({ page }) => {
  const sent = await mockApi(page);
  await page.goto('/#token=t0k3n');
  await expect(page).toHaveURL(/\/$/); // the token is removed from the address bar
  await expect(page.getByRole('heading', { name: 'Set up WorkDigest' })).toBeVisible();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Get started' }).click();

  // Account: wrong domain, then sign-up with confirmation.
  await page.getByLabel('Company email').fill('me@gmail.com');
  await page.getByLabel('Choose a password').fill('longenough');
  await page.getByRole('main').getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('alert')).toContainText('@ei.study');
  await checkA11y(page);
  await page.getByLabel('Company email').fill('arjun.k@ei.study');
  await page.getByRole('main').getByRole('button', { name: 'Create account' }).click();
  await expect(page.getByRole('heading', { name: 'Check your inbox' })).toBeVisible();
  await page.getByRole('main').getByRole('button', { name: "I've confirmed my email" }).click();
  await expect(page.getByRole('alert')).toContainText('not confirmed yet');
  await page.getByRole('main').getByRole('button', { name: "I've confirmed my email" }).click();

  // Profile: git details are suggested.
  await expect(page.getByLabel('Your name')).toHaveValue('Arjun K');
  await page.getByLabel("Your manager's first name").fill('Sana');
  await page.getByLabel('When do you usually send your digest?').fill('18:00');
  await expect(page.getByText('prepared two hours earlier (16:00)')).toBeVisible();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Save and continue' }).click();
  expect(sent['profile']).toMatchObject({ emails: ['arjun.k@ei.study'], manager_name: 'Sana', send_by: '18:00' });

  // Conversation file.
  await page.locator('input[type=file]').setInputFiles({ name: 'chat.txt', mimeType: 'text/plain', buffer: Buffer.from('Me:\nHi Sana,\n') });
  await expect(page.getByText('What we learned from 2 digests and 1 replies')).toBeVisible();
  await expect(page.getByText('your own section')).toBeVisible();
  await expect(page.getByText('Hi Sana,')).toBeVisible();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Use this format' }).click();

  // Repos.
  await page.getByRole('main').getByRole('button', { name: 'Find repositories' }).click();
  await page.getByRole('checkbox', { name: /billing-service/ }).check();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Use 1 repository' }).click();
  expect(sent['repos']).toEqual({ paths: ['/Users/a/Desktop/billing-service'] });

  // Atlassian: tokens are cleared from the page after saving.
  await page.getByLabel(/Jira token/).fill('jira-secret');
  await page.getByLabel(/Bitbucket token/).fill('bb-secret');
  await page.getByRole('main').getByRole('button', { name: 'Save and test' }).click();
  await expect(page.getByText('Works, as Arjun K').first()).toBeVisible();
  await expect(page.getByLabel(/Jira token/)).toHaveValue('');
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Continue' }).click();

  // AI: Claude is locked with the login help; Cursor is chosen.
  await expect(page.getByText('Signed out', { exact: true })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Claude Code/ })).toBeDisabled();
  await expect(page.getByRole('radio', { name: /Cursor/ })).toBeChecked();
  await expect(page.getByText('claude', { exact: true })).toBeVisible();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Continue' }).click();
  expect(sent['ai']).toEqual({ provider: 'cursor_cli' });

  // Hooks, start, phone.
  await page.getByRole('main').getByRole('button', { name: 'Install and continue' }).click();
  await page.getByRole('main').getByRole('button', { name: 'Start WorkDigest' }).click();
  await expect(page.getByText('Connected.')).toBeVisible({ timeout: 10_000 });
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('img', { name: /QR code/ })).toBeVisible();
  await checkA11y(page);
  await page.getByRole('main').getByRole('button', { name: 'Finish' }).click();
  await expect(page.getByRole('heading', { name: "You're all set" })).toBeVisible();
});

test('the page never scrolls sideways', async ({ page }) => {
  await mockApi(page);
  await page.goto('/#token=t0k3n');
  await expect(page.getByRole('heading', { name: 'Set up WorkDigest' })).toBeVisible();
  const width = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(width).toBeLessThanOrEqual(0);
});

test('an expired link says what to do', async ({ page }) => {
  await page.route('**/api/**', (route) => route.fulfill({ status: 401, json: { error: 'this setup link has expired; run `digest setup` again' } }));
  await page.goto('/#token=old');
  await expect(page.getByRole('alert').first()).toContainText('digest setup');
});
