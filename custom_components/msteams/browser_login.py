"""Microsoft login using HA's rbw field API and browser-daemon's passkey signer."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

from browser_daemon_client import BrowserDaemon, BrowserDaemonError, RunResult

MICROSOFT_ORIGINS = (
    "https://login.microsoftonline.com",
    "https://login.microsoft.com",
    "https://teams.microsoft.com",
    "https://teams.cloud.microsoft",
)
LOGIN_TIMEOUT_SECONDS = 150
PASSKEY_TIMEOUT_SECONDS = 60
DISARM_TIMEOUT_SECONDS = 5

PASSKEY_LOGIN_SCRIPT = r"""async ({ page, args }) => {
  const allowed = new Set(args.origins);
  const deadline = Date.now() + args.timeoutMs;
  let enteredUsername = false;
  let passkeySelected = false;
  let openedMethods = false;
  let kmsiHandled = false;
  let lastStep = 'start';
  const initialBlankDeadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    try {
      let currentUrl;
      try { currentUrl = new URL(page.url()); }
      catch { return { outcome: 'needs_interaction', reason: 'invalid_current_url' }; }
      if (!allowed.has(currentUrl.origin)) {
        if (currentUrl.href === 'about:blank') {
          if (Date.now() < initialBlankDeadline) {
            await page.waitForTimeout(200);
            continue;
          }
          return { outcome: 'needs_interaction', reason: 'initial_blank_timeout' };
        }
        return { outcome: 'needs_interaction', reason: 'origin_not_allowed' };
      }
      if (
        currentUrl.origin === 'https://login.microsoft.com' &&
        currentUrl.pathname === '/common/bridge/fido'
      ) {
        lastStep = 'passkey_redirect_seen';
      }
      if (await page.locator('[data-tid="app-bar"], [data-tid="app-layout-area--main"]').first().isVisible())
        return { outcome: 'authenticated', reason: 'app_visible' };
      const body = (await page.locator('body').innerText()).toLowerCase();
      const humanApproval = [
        /approve[ -]sign[ -]in request/,
        /check your (microsoft )?authenticator app/,
        /approve the request/,
        /sent a notification to your mobile device/,
        /more information required/,
        /let.s protect your account/,
      ].some(pattern => pattern.test(body));
      if (humanApproval)
        return { outcome: 'needs_interaction', reason: 'human_approval_required' };
      if (
        currentUrl.origin === 'https://login.microsoftonline.com' &&
        !kmsiHandled
      ) {
        const staySignedIn = page.getByText(/stay signed in\?/i).first();
        if (await staySignedIn.isVisible()) {
          const yes = page.getByRole('button', { name: 'Yes', exact: true });
          if (!(await yes.isVisible()))
            return { outcome: 'needs_interaction', reason: 'kmsi_control_mismatch' };
          await yes.click({ noWaitAfter: true, timeout: 5000 });
          kmsiHandled = true;
          lastStep = 'kmsi_yes_clicked';
          await page.waitForTimeout(400);
          continue;
        }
      }
      if (await page.locator('#passwordError, #usernameError, #idDiv_SAOTCC_Error').first().isVisible())
        return { outcome: 'failed', reason: 'login_error_visible' };
      const password = page.locator('input[type="password"]').first();
      const methodNames = /^(sign.in options|other ways to sign in|sign in another way)$/i;
      const otherMethods = page.getByRole('link', { name: methodNames })
        .or(page.getByRole('button', { name: methodNames }))
        .or(page.getByText(methodNames)).filter({ visible: true }).first();
      const passkeyNames = /passkey|security key|face.*fingerprint/i;
      const passkeyMethod = page.getByRole('button', { name: passkeyNames })
        .or(page.getByRole('link', { name: passkeyNames }))
        .or(page.getByText(/^(face, fingerprint, PIN or security key|use a passkey|use a security key)$/i))
        .filter({ visible: true }).first();
      const passkeyAvailable = await passkeyMethod.isVisible() || await otherMethods.isVisible();
      if (await password.isVisible() && !passkeyAvailable && !passkeySelected)
        return { outcome: 'password_fallback', reason: 'password_form_ready' };

      const username = page.locator('input[name="loginfmt"]');
      if (!enteredUsername && await username.isVisible()) {
        await username.fill(args.username);
        await page.locator('#idSIButton9').click({ noWaitAfter: true, timeout: 5000 });
        enteredUsername = true;
        lastStep = 'username_submitted';
      } else if (!passkeySelected) {
        if (await passkeyMethod.isVisible()) {
          await passkeyMethod.click({ noWaitAfter: true, timeout: 5000 });
          passkeySelected = true;
          lastStep = 'passkey_selected';
        } else if (!openedMethods) {
          if (await otherMethods.isVisible()) {
            await otherMethods.click({ noWaitAfter: true, timeout: 5000 });
            openedMethods = true;
            lastStep = 'methods_opened';
          }
        } else {
          const passwordChoice = page.locator(
            '#idA_PWD_SwitchToPassword, [data-value="Password"], [data-value="password"]'
          ).first();
          if (await passwordChoice.isVisible()) {
            await passwordChoice.click({ noWaitAfter: true, timeout: 5000 });
            lastStep = 'password_selected';
          } else {
            const labeledPassword = page.getByText(
              /^(use your password|enter password|password)$/i
            ).first();
            if (await labeledPassword.isVisible()) {
              await labeledPassword.click({ noWaitAfter: true, timeout: 5000 });
              lastStep = 'password_selected';
            }
          }
        }
      }
    } catch (error) {
      if (error.name !== 'TimeoutError' && !/Execution context was destroyed|Cannot find context with specified id/.test(error.message))
        throw error;
    }
    await page.waitForTimeout(400);
  }
  const deadlineReasons = {
    start: 'deadline_before_username',
    username_submitted: 'deadline_after_username',
    passkey_selected: 'deadline_after_passkey',
    passkey_redirect_seen: 'deadline_after_passkey_redirect',
    kmsi_yes_clicked: 'deadline_after_kmsi',
    methods_opened: 'deadline_after_methods',
    password_selected: 'deadline_after_password_choice',
  };
  return {
    outcome: 'needs_interaction',
    reason: deadlineReasons[lastStep] || 'passkey_script_deadline',
  };
}"""


class TeamsBrowserDaemon(BrowserDaemon):
    async def session_info(self, session_id: str, node: str | None = None) -> dict[str, Any]:
        """Get session metadata without colliding with BrowserDaemon's HTTP session field."""
        return await self._json(
            "GET",
            f"/api/sessions/{quote(session_id, safe='')}",
            node=node,
        )

    async def arm_passkeys(self, session_id: str, node: str | None, *, enabled: bool) -> None:
        await self._json(
            "POST",
            f"/api/sessions/{quote(session_id, safe='')}/agent-passkey",
            node=node,
            json={"sites": ["microsoft.com"] if enabled else [], "seconds": 90 if enabled else 0},
        )

    async def password_login(
        self,
        session_id: str,
        tab_id: str,
        username: str,
        password: str,
        *,
        totp_code: str | None = None,
        node: str | None = None,
    ) -> str:
        """Submit Microsoft credentials through browser-daemon's REST API."""
        payload: dict[str, Any] = {
            "tab": tab_id,
            "username": username,
            "password": password,
            "allowed_origins": list(MICROSOFT_ORIGINS),
            "success_selector": '[data-tid="app-bar"], [data-tid="app-layout-area--main"]',
            "timeout_seconds": 30,
        }
        if totp_code is not None:
            payload["totp_code"] = totp_code
        response = await self._json(
            "POST",
            f"/api/sessions/{quote(session_id, safe='')}/login",
            node=node,
            timeout=35,
            json=payload,
        )
        outcomes = {"authenticated", "needs_totp", "needs_interaction", "failed", "unknown"}
        if not isinstance(response, dict):
            return "needs_interaction"
        outcome = response.get("outcome")
        return outcome if isinstance(outcome, str) and outcome in outcomes else "needs_interaction"


def _set_login_diagnostic(
    client: TeamsBrowserDaemon, reason: str, error: Exception | None = None
) -> None:
    """Expose fixed branch codes and exception type only to local diagnostics."""
    client.login_diagnostic_reason = reason
    client.login_diagnostic_exception_type = type(error).__name__ if error else "none"


async def async_login(
    hass: Any,
    client: TeamsBrowserDaemon,
    session_id: str,
    tab_id: str,
    node: str | None,
    rbw_entry_id: str,
    rbw_item_id: str,
    *,
    prefer_passkey: bool = True,
) -> tuple[str, str | None]:
    from custom_components.rbw import async_get_field

    _set_login_diagnostic(client, "login_started")

    async def read_field(field: str) -> str | None:
        result = await async_get_field(hass, rbw_entry_id, rbw_item_id, field)
        value = result.get("value") if isinstance(result, dict) else None
        return value if isinstance(value, str) and value else None

    armed_attempted = False

    async def try_passkey(username: str) -> str:
        nonlocal armed_attempted
        try:
            response = await client.session_info(session_id, node=node)
            session = response.get("session") if isinstance(response, dict) else None
        except Exception as err:
            _set_login_diagnostic(client, "session_info_exception", err)
            return "needs_interaction"
        # Arming affects every page in the session. Do not change a session that
        # already has a live CDP client attached.
        if not isinstance(session, dict) or session.get("client_count") != 0:
            _set_login_diagnostic(client, "session_in_use")
            return "needs_interaction"

        armed_attempted = True
        try:
            try:
                await client.arm_passkeys(session_id, node, enabled=True)
            except Exception as err:
                unavailable = isinstance(err, BrowserDaemonError) and err.status == 503
                reason = "passkey_service_unavailable" if unavailable else "passkey_arm_exception"
                _set_login_diagnostic(client, reason, err)
                return "password_fallback" if unavailable else "needs_interaction"

            try:
                result = await client.run(
                    session_id=session_id,
                    tab=tab_id,
                    node=node,
                    script=PASSKEY_LOGIN_SCRIPT,
                    args={
                        "username": username,
                        "origins": list(MICROSOFT_ORIGINS),
                        "timeoutMs": PASSKEY_TIMEOUT_SECONDS * 1000,
                    },
                    timeout=PASSKEY_TIMEOUT_SECONDS + 5,
                    screenshot_on_error=False,
                    check=False,
                )
            except Exception as err:
                _set_login_diagnostic(client, "passkey_run_exception", err)
                return "needs_interaction"

            if not isinstance(result, RunResult):
                _set_login_diagnostic(client, "passkey_run_result_type")
                return "needs_interaction"
            if not result.ok:
                reason = "passkey_run_timeout" if result.timed_out else "passkey_run_failed"
                _set_login_diagnostic(client, reason)
                return "needs_interaction"
            if not isinstance(result.result, dict):
                _set_login_diagnostic(client, "passkey_run_payload_type")
                return "needs_interaction"
            outcome = result.result.get("outcome")
            diagnostics = {
                "authenticated": "script_authenticated",
                "failed": "script_failed",
                "needs_interaction": "script_needs_interaction",
                "password_fallback": "script_password_fallback",
            }
            if isinstance(outcome, str) and outcome in diagnostics:
                script_reasons = {
                    "app_visible": "app_visible",
                    "deadline_after_kmsi": "deadline_after_kmsi",
                    "deadline_after_methods": "deadline_after_methods",
                    "deadline_after_passkey": "deadline_after_passkey",
                    "deadline_after_passkey_redirect": "deadline_after_passkey_redirect",
                    "deadline_after_password_choice": "deadline_after_password_choice",
                    "deadline_after_username": "deadline_after_username",
                    "deadline_before_username": "deadline_before_username",
                    "human_approval_required": "human_approval_required",
                    "initial_blank_timeout": "initial_blank_timeout",
                    "invalid_current_url": "invalid_current_url",
                    "kmsi_control_mismatch": "kmsi_control_mismatch",
                    "login_error_visible": "login_error_visible",
                    "origin_not_allowed": "origin_not_allowed",
                    "passkey_script_deadline": "passkey_script_deadline",
                    "password_form_ready": "password_form_ready",
                }
                script_reason = result.result.get("reason")
                diagnostic = (
                    script_reasons[script_reason]
                    if isinstance(script_reason, str) and script_reason in script_reasons
                    else diagnostics[outcome]
                )
                _set_login_diagnostic(client, diagnostic)
                return outcome
            _set_login_diagnostic(client, "script_unknown_outcome")
            return "needs_interaction"
        finally:
            await disarm_safely()

    async def disarm_safely() -> None:
        nonlocal armed_attempted
        if not armed_attempted:
            return
        armed_attempted = False
        try:
            await asyncio.wait_for(
                asyncio.shield(client.arm_passkeys(session_id, node, enabled=False)),
                timeout=DISARM_TIMEOUT_SECONDS,
            )
        except BaseException:
            # The API expires an arm after 90 seconds even if disarming fails.
            # Never replace the login result, failure, timeout, or cancellation.
            return

    async def login() -> tuple[str, str | None]:
        username = await read_field("username")
        if not username:
            return "failed", username

        if prefer_passkey:
            outcome = await try_passkey(username)
            if outcome != "password_fallback":
                return outcome, username

        password = await read_field("password")
        if not password:
            return "needs_interaction", username

        outcome = await client.password_login(
            session_id,
            tab_id,
            username,
            password,
            node=node,
        )
        if outcome == "needs_totp":
            code = await read_field("totp")
            if code:
                outcome = await client.password_login(
                    session_id,
                    tab_id,
                    username,
                    password,
                    totp_code=code,
                    node=node,
                )
        return outcome, username

    try:
        async with asyncio.timeout(LOGIN_TIMEOUT_SECONDS):
            return await login()
    finally:
        await disarm_safely()
