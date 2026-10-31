/** Node adapter for the Teams browser-daemon authentication library. */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);
const sameSite = { strict: 'Strict', lax: 'Lax', none: 'None' };

export function browserDaemonConfig() {
  const url = process.env.TEAMS_MCP_BROWSER_DAEMON_URL;
  if (!url) return null;
  return { url: url.replace(/\/+$/, ''), profile: process.env.TEAMS_MCP_BROWSER_DAEMON_PROFILE || 'work' };
}

export function toSessionState(login) {
  return {
    cookies: (login.cookies || []).map(cookie => ({
      name: cookie.name, value: cookie.value, domain: cookie.domain,
      path: cookie.path || '/', expires: typeof cookie.expires === 'number' && cookie.expires > 0 ? cookie.expires : -1,
      httpOnly: cookie.http_only ?? false, secure: cookie.secure ?? true,
      sameSite: sameSite[(cookie.same_site || '').toLowerCase()] || 'Lax',
    })),
    origins: [{ origin: new URL(login.url).origin, localStorage: Object.entries(login.local_storage || {}).map(([name, value]) => ({ name, value })) }],
  };
}

export async function loginViaBrowserDaemon(config) {
  const python = process.env.TEAMS_AUTH_SHIM_PYTHON || '@TEAMS_AUTH_PYTHON@';
  const shim = process.env.TEAMS_AUTH_SHIM_PATH || '@TEAMS_AUTH_PATH@';
  if (!python || !shim) throw new Error('Teams authentication library is not configured');
  try {
    const { stdout } = await run(python, [shim], {
      env: { ...process.env, TEAMS_MCP_BROWSER_DAEMON_URL: config.url, TEAMS_MCP_BROWSER_DAEMON_PROFILE: config.profile },
      timeout: 90_000, maxBuffer: 8 * 1024 * 1024,
    });
    return toSessionState(JSON.parse(stdout));
  } catch {
    // Child output is authentication material. Never include it in an error.
    throw new Error('Teams browser-daemon authentication failed');
  }
}
