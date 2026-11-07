# Troubleshooting

## Entities show unavailable or `Connected` is off

- Check that Home Assistant can reach Microsoft Teams and that the selected
  browser-daemon profile is signed in to the intended account.
- Use the authentication repair flow or integration **Reconfigure** to
  reacquire authentication. Reconfigure checks that the account matches the
  existing entry.
- The **Refresh tokens** button tries token refresh immediately. It does not
  complete a human sign-in prompt.

## Browser sign-in stops for interaction

Open the selected browser-daemon profile and complete any Microsoft approval,
MFA, consent, password-change, or account-selection step. Then retry the
Home Assistant flow. If recovery created a new browser session, it remains
available for 15 minutes; an attached profile session retains its existing
timeout.

## rbw sign-in does not proceed

- Check that the rbw integration and the Microsoft account item are selected, and
  that the rbw integration's options allow Teams to read that item.
- Confirm the selected rbw client can read the item's `username`, `password`,
  and current `totp` fields. Keep access limited to those fields.
- Confirm the browser-daemon profile is not already attached to another live
  browser client if passkey sign-in is enabled. The signer will not arm passkeys
  in a session with an active client.
- Microsoft may require a sign-in method or human action that automated
  password/TOTP entry cannot complete. Finish it in the browser profile.

## Translations or labels look stale

Restart Home Assistant, then open a new frontend tab. Existing frontend tabs can
keep previously loaded translations.

## Sharing diagnostics

The diagnostic export redacts token fields, but coordinator data can include
account names, meeting details, participants, and join URLs. Review the export
and remove personal meeting or account information before sharing it. Never
share browser session URLs, exported authentication state, refresh tokens,
passwords, TOTP values, or browser-daemon secrets.
