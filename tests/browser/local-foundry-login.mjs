// Run after npm run build in apps/agent-dev-board-web.
// PLAYWRIGHT_MODULE can point at an operator's existing Playwright installation.
import { createServer } from 'node:http';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const { chromium, expect } = await import(process.env.PLAYWRIGHT_MODULE || '@playwright/test');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const dist = path.join(root, 'apps/agent-dev-board-web/dist');
const server = createServer(async (req, res) => {
  const name = new URL(req.url, 'http://local').pathname;
  const filename = path.join(dist, name === '/' ? 'index.html' : name);
  try {
    if (!filename.startsWith(dist + '/')) throw new Error('bad path');
    const data = await fs.readFile(filename);
    res.setHeader('Content-Type', name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html');
    res.end(data);
  } catch { res.writeHead(404); res.end(); }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const scenario of ['success', 'failure', 'expiry', 'late-login', 'ticket-failure', 'switch-method']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const calls = [], errors = [], external = [];
    let releaseLogin;
    const waitLogin = new Promise(resolve => { releaseLogin = resolve; });
    page.on('pageerror', error => errors.push(error.message));
    page.on('request', request => {
      if (!['127.0.0.1', 'localhost'].includes(new URL(request.url()).hostname)) external.push(request.url());
    });
    await page.route('**/api/**', async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      const body = request.postData() ? request.postDataJSON() : {};
      calls.push({ pathname, body });
      const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
      const agent = { name: 'local-source', label: 'Local source', base_url: 'http://127.0.0.1:8085', status: 'stopped', template_id: 'me_agent' };
      if (pathname === '/api/local-agent-templates') return json([{ id: 'me_agent', label: 'Example', description: '' }]);
      if (pathname === '/api/local-agents' || pathname === '/api/agents') return json([agent]);
      if (pathname === '/api/developer/context') return json({ git: {}, github: {}, developer_identity: {}, foundry: { url: body.foundry_url, routes: {} } });
      if (pathname === '/api/handshake/probe') return json({ agent: { ...agent, bootstrap_state: {}, agent_card: {} }, foundry: { url: body.foundry_url, routes: {} }, checks: [] });
      if (pathname === '/api/cloud-run/deployments') return json([]);
      if (pathname === '/api/cloud-run/runtimes') return json({ services: [], jobs: [] });
      if (pathname === '/api/developer/foundry-login') {
        expect(body.password).toBe('fixture-browser-password');
        if (scenario === 'late-login') await waitLogin;
        if (scenario === 'failure') return json({ detail: 'Foundry sign-in was rejected' }, 401);
        return json({ ok: true, session_id: `opaque-${scenario}`, foundry_url: body.foundry_url,
          developer_identity: { provider: 'foundry', username: 'local-developer' },
          expires_at: new Date(Date.now() + (scenario === 'expiry' ? 1600 : 600000)).toISOString() });
      }
      if (pathname === '/api/developer/bootstrap-ticket') {
        expect(body.foundry_session_id).toBe(`opaque-${scenario}`);
        expect(body.developer_token).toBe(''); expect(body.github_token).toBe('');
        return json(scenario === 'ticket-failure' ? { ok: false, message: 'Foundry rejected this developer session' } : { ok: true, claim_applied: true });
      }
      if (pathname === '/api/settlements') return json({ settlements: [], meta: {} });
      return json({ ok: true });
    });
    await page.goto(origin);
    const loginPanel = page.locator('section.panel').filter({ has: page.getByRole('heading', { name: 'Log in as a developer', exact: true }) });
    await expect(loginPanel).toBeVisible();
    await expect(loginPanel.getByLabel('Developer login method')).toHaveValue('foundry');
    await loginPanel.getByLabel('Foundry URL', { exact: true }).fill('https://private-foundry.test');
    await loginPanel.getByLabel('Foundry username', { exact: true }).fill('local-developer');
    await loginPanel.getByLabel('Foundry password', { exact: true }).fill('fixture-browser-password');
    await loginPanel.getByRole('button', { name: 'Sign in to Foundry', exact: true }).click();
    await expect(loginPanel.getByLabel('Foundry password', { exact: true })).toHaveValue('');
    const ticket = page.getByRole('button', { name: 'Request Foundry ticket', exact: true });
    if (scenario === 'late-login') {
      await expect.poll(() => calls.filter(call => call.pathname.endsWith('/foundry-login')).length).toBe(1);
      await loginPanel.getByLabel('Foundry URL', { exact: true }).fill('https://another-foundry.test');
      releaseLogin();
      await expect.poll(() => calls.some(call => call.pathname.endsWith('/foundry-logout'))).toBe(true);
      await expect(ticket).toBeDisabled();
    } else if (scenario === 'failure') {
      await expect(page.getByText('Error: Foundry sign-in was rejected', { exact: true })).toBeVisible();
      await expect(ticket).toBeDisabled();
    } else {
      await expect(ticket).toBeEnabled();
      if (scenario === 'expiry') {
        await expect(ticket).toBeDisabled({ timeout: 7000 });
        await expect(page.getByText('Foundry developer session expired. Sign in again.', { exact: true })).toBeVisible();
      } else if (scenario === 'switch-method') {
        await loginPanel.getByLabel('Developer login method').selectOption('github');
        await expect(ticket).toBeDisabled();
        await expect(loginPanel.getByRole('button', { name: 'Login with GitHub', exact: true })).toBeVisible();
        await expect.poll(() => calls.some(call => call.pathname.endsWith('/foundry-logout'))).toBe(true);
      } else {
        await ticket.click();
        await expect.poll(() => calls.filter(call => call.pathname.endsWith('/bootstrap-ticket')).length).toBe(1);
        if (scenario === 'ticket-failure') {
          await expect(page.getByText('Error: Foundry rejected this developer session', { exact: true })).toBeVisible();
        }
        await loginPanel.getByLabel('Foundry URL', { exact: true }).fill('https://another-foundry.test');
        await expect(ticket).toBeDisabled();
        await expect.poll(() => calls.some(call => call.pathname.endsWith('/foundry-logout'))).toBe(true);
      }
    }
    expect(await page.evaluate(() => ({ local: Object.keys(localStorage), session: Object.keys(sessionStorage) }))).toEqual({ local: [], session: [] });
    expect(errors).toEqual([]); expect(external).toEqual([]);
    expect(calls.some(call => call.pathname.includes('github'))).toBe(false);
    results.push({ scenario, passed: true, browser_storage_empty: true, external_requests: 0 });
    await page.close();
  }
  console.log(JSON.stringify({ results }, null, 2));
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
