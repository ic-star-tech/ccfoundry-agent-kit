// Browser fixtures; actual Docker/API integration is tests/containers/customer_project.py.
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
const browser = await chromium.launch({ headless: true });
const results = [];
try {
  for (const scenario of ['build-export-push', 'build-error', 'source-switch']) {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const calls = [], errors = [];
    let build = {}, runtime = 'stopped';
    const imageId = 'sha256:' + '1'.repeat(64);
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/api/**', async route => {
      const request = route.request(), url = new URL(request.url()), pathname = url.pathname;
      const body = request.postData() ? request.postDataJSON() : {};
      calls.push({pathname, body, method:request.method()});
      const json = (value, status = 200) => route.fulfill({ status, contentType:'application/json', body:JSON.stringify(value) });
      const agent = {name:'customer_one', label:'Customer one', source_kind:'project', project_path:'/customer/source', app_module:'customer:app', base_url:'http://127.0.0.1:8085', template_id:'project_fixture', status:runtime, project_build_id:build.image_id ? 'b'.repeat(32) : ''};
      const other = {...agent, name:'customer_two', label:'Customer two', project_build_id:''};
      if (pathname === '/api/local-agent-templates') return json([{id:'project_fixture', kind:'project', label:'Own source', description:'Customer Python'}]);
      if (pathname === '/api/local-agents' || pathname === '/api/agents') return json([agent, other]);
      if (pathname === '/api/cloud-run/deployments') return json([]);
      if (pathname === '/api/cloud-run/runtimes') return json({services:[], jobs:[]});
      if (pathname === '/api/developer/context') return json({git:{},github:{},developer_identity:{},foundry:{routes:{}}});
      if (pathname === '/api/handshake/probe') return json({agent:{...agent,bootstrap_state:{},agent_card:{}},foundry:{routes:{}},checks:[]});
      if (pathname === '/api/settlements') return json({settlements:[],meta:{}});
      if (pathname.endsWith('/container/build')) {
        if (request.method() === 'GET') return json(pathname.includes('customer_two') ? {} : build);
        if (scenario === 'build-error') return json({detail:'Offline image builds require a wheelhouse'},400);
        build = {image_id:imageId, platform:'linux/arm64', manifest_path:'/board/build/agentservice.yaml', log_path:'/board/build/build.log'};
        return json(build);
      }
      if (pathname.endsWith('/container/start')) {runtime='running';return json({...agent,status:runtime,runtime_kind:'container'});}
      if (pathname.endsWith('/container/export')) return json({archive_path:'/board/build/image.tar',sha256:'2'.repeat(64)});
      if (pathname.endsWith('/container/push')) return json({repo_digest:'registry.internal/team/customer@sha256:'+'3'.repeat(64)});
      return json({ok:true});
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    const panel = page.getByRole('region',{name:'Customer project container'});
    await expect(panel).toBeVisible();
    await expect(page.getByRole('button',{name:/Cloud Run pull worker \(built-in templates\)/})).toBeDisabled();
    await expect(panel.getByRole('button',{name:'Start local container',exact:true})).toBeDisabled();
    await panel.getByLabel('Build image tag').fill('local/customer:0.2.0');
    await panel.getByLabel('Image architecture').selectOption('linux/arm64');
    await panel.getByLabel('Build dependencies').selectOption('offline');
    await panel.getByRole('button',{name:'Build customer image'}).click();
    if (scenario === 'build-error') {
      await expect(panel.getByRole('alert')).toContainText('require a wheelhouse');
      await expect(panel.getByRole('button',{name:'Start local container',exact:true})).toBeDisabled();
    } else {
      await expect(panel).toContainText(imageId);
      const buildCall=calls.find(c=>c.pathname.endsWith('/container/build')&&c.method==='POST');
      expect(buildCall.body).toEqual({image_ref:'local/customer:0.2.0',platform:'linux/arm64',offline:true});
      if (scenario === 'build-export-push') {
        await panel.getByRole('button',{name:'Export image archive'}).click();
        await expect(panel).toContainText('/board/build/image.tar');
        await panel.getByLabel('Private registry destination').fill('registry.internal/team/customer:0.2.0');
        await panel.getByRole('button',{name:'Push to private registry'}).click();
        await expect(panel).toContainText('registry.internal/team/customer@sha256:');
        expect(calls.find(c=>c.pathname.endsWith('/container/push')).body.target_ref).toBe('registry.internal/team/customer:0.2.0');
        await panel.getByRole('button',{name:'Start local container',exact:true}).click();
        await expect(panel.getByRole('button',{name:'Start local container',exact:true})).toBeDisabled();
      } else {
        const selector = page.locator('select').filter({has:page.locator('option[value="customer_two"]')}).first();
        await selector.selectOption('customer_two');
        await expect(panel.getByRole('button',{name:'Start local container',exact:true})).toBeDisabled();
        await expect(panel).not.toContainText(imageId);
      }
    }
    expect(errors).toEqual([]);
    results.push({scenario,passed:true});
    await page.close();
  }
  console.log(JSON.stringify({passed:results.length,results},null,2));
} finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
