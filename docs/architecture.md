# Architecture

Home Assistant owns the integration entry, coordinator, and entity state. The
integration calls Microsoft Teams APIs directly for presence, meetings, chats,
messages, and reactions. Browser-daemon provides browser session management for
authentication and recovery; routine token refreshes do not open a browser.

## Data updates

The coordinator polls at the configured interval, which defaults to 30 seconds
and can be set from 5 to 300 seconds. Polling remains active when push updates
are disabled.

When enabled, a separate Teams push connection watches for relevant call and
chat activity. Push messages act as hints: they schedule a coordinator refresh,
and the Teams API remains the source of entity data. The push connection retries
after failures while polling continues.

## Authentication lifecycle

The initial sign-in and recovery path obtains authentication state from the
selected browser-daemon profile, validates the resulting Teams account, and
stores the OAuth refresh state in the Home Assistant config entry. Home
Assistant uses the refresh token for normal access-token renewal. If that
refresh fails, browser-daemon and optionally rbw can be used to sign in again.

The integration reuses the Home Assistant browser-daemon client when available.
For rbw passkeys, browser-daemon's rbw secret agent performs the signing. The
integration reads username, password, and current TOTP fields from the Home
Assistant rbw API only when required by the login flow. See
[Authentication](authentication.md).

If recovery creates a new browser-daemon session and leaves it open for human
action, that session receives a 15-minute idle timeout. Existing sessions keep
their configured timeout.
