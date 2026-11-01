# Agent instructions

## Project

This Home Assistant integration uses browser-daemon session management to expose Microsoft Teams presence, meetings, chats, notifications, and actions.

## Working in this repository

- Read `README.md` before changing browser authentication, meeting events, or message services; the integration depends on Teams behavior described there.
- Keep session and message contents out of logs, fixtures, and commits.
- Before changing CI, check [pschmitt/ci-workflows](https://github.com/pschmitt/ci-workflows) for reusable components and preserve this repository's existing checks.

## Validation

- Run `uvx --with aiohttp --with voluptuous --with pytest --with pytest-asyncio pytest` for the Python tests.
- Run `uvx ruff check .` and `uvx ruff format --check .` for Python changes; CI also runs Hassfest.
