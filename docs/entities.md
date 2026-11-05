# Entities

Home Assistant creates these entities for each configured Teams account. The
entity registry assigns entity IDs; the names below are the integration's
translated entity names.

## Sensors

| Name | State and attributes |
| --- | --- |
| Presence | Current availability. Attributes include activity and custom status message. |
| Next meeting | Upcoming meeting subject, or `No upcoming meetings`. Attributes include meeting ID, start and end times, organizer, location, description, participants, join URL, and response status when available. |
| Unread chats | Count of chat conversations with unread messages. |

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
