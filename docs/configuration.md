# Configuration

## Initial setup

1. Configure the browser-daemon integration in Home Assistant and sign in to a
   dedicated browser profile for the Microsoft Teams account.
2. Add **Microsoft Teams** from **Settings** → **Devices & Services** →
   **Add Integration**.
3. Enable browser-daemon authentication and enter the profile name. Home
   Assistant reuses its configured browser-daemon connection when available.
4. Optionally select an rbw integration and provide the item ID for the
   Microsoft account. Select both fields or leave both empty. See
   [Authentication](authentication.md) for the fields and access scope.
5. Submit the form. If Microsoft requests approval, MFA, consent, or another
   interactive step, complete it in the browser-daemon profile and retry.

The integration can also be configured with a credentials file or an OAuth
refresh token. Browser-daemon authentication is the recommended path for
interactive recovery. Do not put credentials or tokens in shared YAML or
automation examples.

## Reconfigure authentication

Open the integration's menu and choose **Reconfigure** to change the profile or
rbw settings. Reconfiguration signs in and validates the account before saving.
It checks the returned Teams identity against the existing account and rejects
a different account.

## Options

Open the integration's **Options** to set:

- **Update interval**: Polling interval in seconds, from 5 to 300. The default
  is 30 seconds.
- **Enable real-time push updates**: Enabled by default. Disable it to use
  polling only.
- **Default conversation ID**: Conversation used by notifications and by
  `msteams.send_message` when that service omits a conversation ID.
- **rbw integration and item**: Credential source used for browser sign-in.
  Both settings must be selected together or cleared together.
- **Prefer rbw passkey sign-in**: Try the browser-daemon passkey signer before
  password and TOTP fallback.

Changes to options reload the integration. For update behavior, see
[Architecture](architecture.md).
