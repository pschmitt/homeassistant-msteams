import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { browserDaemonConfig, loginViaBrowserDaemon, toSessionState } from '../custom_components/msteams/browser_auth.mjs';

test('translates browser auth to MCP session state', () => {
  const state = toSessionState({ url: 'https://teams.microsoft.com/v2', local_storage: { key: 'value' }, cookies: [{ name: 'test', value: 'fake', domain: 'teams.microsoft.com', same_site: 'none' }] });
  assert.equal(state.cookies[0].sameSite, 'None');
  assert.equal(state.origins[0].origin, 'https://teams.microsoft.com');
  assert.deepEqual(state.origins[0].localStorage, [{ name: 'key', value: 'value' }]);
});

test('invokes shared shim and keeps errors generic', async () => {
  const dir = await mkdtemp(join(tmpdir(), 'teams-auth-test-'));
  const path = join(dir, 'shim.mjs');
  const old = { ...process.env };
  try {
    await writeFile(path, `console.log(JSON.stringify({url:'https://teams.microsoft.com',cookies:[],local_storage:{key:'value'}}))`);
    process.env.TEAMS_AUTH_SHIM_PYTHON = process.execPath;
    process.env.TEAMS_AUTH_SHIM_PATH = path;
    process.env.TEAMS_MCP_BROWSER_DAEMON_URL = 'https://browser.example/';
    const config = browserDaemonConfig();
    assert.equal(config.url, 'https://browser.example');
    const state = await loginViaBrowserDaemon(config);
    assert.equal(state.origins[0].localStorage[0].value, 'value');
    await writeFile(path, `console.error('fake-sensitive-output'); process.exit(1)`);
    await assert.rejects(loginViaBrowserDaemon(config), err => !err.message.includes('fake-sensitive-output'));
  } finally {
    for (const key of ['TEAMS_AUTH_SHIM_PYTHON', 'TEAMS_AUTH_SHIM_PATH', 'TEAMS_MCP_BROWSER_DAEMON_URL']) {
      if (old[key] === undefined) delete process.env[key]; else process.env[key] = old[key];
    }
    await rm(dir, { recursive: true, force: true });
  }
});
