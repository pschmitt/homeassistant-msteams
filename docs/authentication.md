# Authentication

## Browser-daemon profile

The integration uses the configured Home Assistant browser-daemon connection
when it is available, including its connection details. It opens or reuses a
Teams page in the selected browser-daemon profile and exports the Teams
authentication state to validate it. Choose a dedicated profile that is signed
into the intended Microsoft account. A profile signed into another account
will not be accepted for an existing integration entry.

The integration compares the authenticated Teams account's stable identity
with the account already configured. Initial setup and native **Reconfigure**
both validate the resulting account before saving. Reconfigure rejects a
different account.

## rbw credentials and passkeys

Select both an rbw integration and its Microsoft account item ID. At login time,
the integration calls Home Assistant rbw's `async_get_field` for the current
`username`, `password`, and `totp` fields as needed. It does not pre-copy a
password or TOTP seed into the Teams entry. A current TOTP code is read from
rbw when Microsoft asks for it.

Grant the selected rbw client access only to the fields it needs for this item:
the username, and the password and current TOTP fields for fallback. Do not
grant unrelated vault items or fields. If the rbw integration offers
field-level permissions, keep the access scoped to this item and these fields.

When **Prefer rbw passkey sign-in** is enabled, the integration tries a passkey
first. Passkey signing is handled by browser-daemon's rbw secret agent. The
Teams integration does not read or store the passkey private key in Home
Assistant. If passkey signing is unavailable and the login flow allows it, the
integration tries the rbw password and, if requested, the current TOTP code.
Automated credential submission has a 150-second deadline; it returns control
for human action when the login flow needs an interactive step.

Microsoft may still require a human to approve a sign-in, complete MFA or
consent, change a password, or resolve another account challenge. The password
and TOTP paths are supported by the integration, but Microsoft may not offer
them in every login flow. Complete any interactive step in the selected
browser-daemon profile and retry the repair or configuration flow.

## Token refresh and browser recovery

During normal operation, Home Assistant refreshes Microsoft access tokens with
the stored OAuth refresh token. This does not open a browser. If token refresh
fails, the integration can try to acquire fresh authentication through the
configured browser-daemon profile and rbw settings. It checks that the
recovered account is still the configured Teams account before accepting it.

Use the diagnostic **Refresh tokens** button to request an immediate token
refresh. If background recovery fails, it waits five minutes before trying
again. Use the Home Assistant authentication repair or **Reconfigure** to
retry after resolving a sign-in prompt.

When recovery needs a browser page and creates a new browser-daemon session,
that new session is kept for 15 minutes so a human can finish sign-in. A session
already attached to the selected profile keeps its existing timeout. The
15-minute timeout does not extend an attached session.

## Configuration controls

Use **Reconfigure** to update authentication settings and validate the account
again. Use **Options** for the polling interval, push updates, default
conversation, rbw integration and item, and passkey preference. See
[Configuration](configuration.md).
