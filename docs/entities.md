# Entities

Home Assistant creates these entities for each configured Teams account. The
entity registry assigns entity IDs; the names below are the integration's
translated entity names.

## Sensors

| Name | State and attributes |
| --- | --- |
| Presence | Current availability. Attributes include activity and custom status message. |
| Next meeting | Upcoming meeting subject, or `No upcoming meetings`. Attributes include meeting ID, start and end times, organizer, location, description, participants, join URL, and response status when available. |
| Unread chats | Count of chat conversations with unread messages. The `messages` attribute lists the newest unread message of each (`time`, `sender`, `channel`, `kind`, `text`, `conversation_id`), newest first. |
| Recent messages | Disabled by default. Number of messages cached; the `messages` attribute holds up to 100 messages, newest first, from the 25 most recently active chats and channels (`time`, `sender`, `from_me`, `channel`, `kind`, `text`, `conversation_id`). Messages are only fetched while the sensor is enabled. Text is plain and cut at 500 characters. |

The `messages` attributes are excluded from the recorder (history), as they are large and contain message text.

## Binary sensors

| Name | State and attributes |
| --- | --- |
| In a meeting | On when the calendar or live call information indicates an active meeting. Attributes include title, join URL, description, participants, and organizer when available. |
| Connected | Diagnostic connectivity state based on the latest update and authentication status. |

## Meeting event

The **Meeting event** entity emits:

- `meeting_started`
- `meeting_ended`
- `meeting_reminder`

Event data includes meeting details when available: meeting ID, title, join URL,
description, times, organizer, location, and participants. The ended event uses
the last known meeting details after the meeting is no longer active.

## Diagnostic button

**Refresh tokens** requests an immediate OAuth token refresh and then asks the
integration to update its data. If that fails, use the authentication repair
flow or reconfigure the integration. See [Authentication](authentication.md).
