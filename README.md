<p align="center">
  <img src="logo.png" width="160" alt="Microsoft Teams for Home Assistant logo">
</p>

<p align="center">
  <a href="https://github.com/pschmitt/homeassistant-msteams/actions/workflows/ci.yaml"><img src="https://github.com/pschmitt/homeassistant-msteams/actions/workflows/ci.yaml/badge.svg" alt="CI"></a>
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom integration"></a>
</p>

<h1 align="center">Microsoft Teams for Home Assistant</h1>

<p align="center">A Home Assistant integration for Teams presence, meetings, chats, notifications, and message actions.</p>

## Features

- **Presence and meetings**: Track presence, unread chats, upcoming meetings, and meeting events. See [Entities](docs/entities.md).
- **Messages and reactions**: Send notifications, chat messages, replies, and reactions. See [Services](docs/services.md).
- **Browser based authentication**: Reuse a browser-daemon profile and recover authentication through Home Assistant. See [Authentication](docs/authentication.md).
- **Polling and push updates**: Configure periodic refreshes and optional real-time update hints. See [Configuration](docs/configuration.md).

## Install

- **HACS**: Add this repository as a custom repository with category **Integration**, install it, and restart Home Assistant.
- **Manual**: Copy `custom_components/msteams` from this repository into the Home Assistant configuration directory at `custom_components/msteams`. Then restart Home Assistant.

See [Installation](docs/installation.md) for details.

## Quick start

1. Set up the browser-daemon integration in Home Assistant and sign in to a dedicated Teams profile.
2. Go to **Settings** → **Devices & Services** → **Add Integration** and select **Microsoft Teams**.
3. Choose browser-daemon authentication, select the profile, and optionally configure an rbw integration and item.
4. Complete any Microsoft sign-in prompt in the browser-daemon profile, then check the new Teams entities.

## Documentation

| Topic | Description |
| --- | --- |
| [Installation](docs/installation.md) | HACS and manual installation instructions. |
| [Configuration](docs/configuration.md) | Add, reconfigure, and adjust integration options. |
| [Authentication](docs/authentication.md) | Browser-daemon, rbw, token refresh, and account safety. |
| [Entities](docs/entities.md) | Sensors, binary sensors, meeting events, and diagnostic entities. |
| [Services](docs/services.md) | Message, reply, reaction, and notification actions. |
| [Architecture](docs/architecture.md) | Data updates, push connections, and token lifecycle. |
| [Troubleshooting](docs/troubleshooting.md) | Safe checks for common setup and authentication problems. |

## License

MIT. See [LICENSE](LICENSE).
