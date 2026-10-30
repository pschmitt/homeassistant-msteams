"""DataUpdateCoordinator for Microsoft Teams integration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import TeamsApiClient, TeamsAuthError
from .const import (
    CONF_POLL_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_MEETING_ENDED,
    EVENT_MEETING_REMINDER,
    EVENT_MEETING_STARTED,
)

_LOGGER = logging.getLogger(__name__)


def _parse_iso(iso_str: str | None) -> datetime | None:
    """Safely parse ISO datetime string."""
    if not iso_str:
        return None
    try:
        # Replace trailing Z with UTC timezone
        cleaned = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return None


class TeamsDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage fetching Teams data from private APIs."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TeamsApiClient,
    ) -> None:
        self.entry = entry
        self.client = client
        poll_interval = entry.options.get(
            CONF_POLL_INTERVAL,
            entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"Microsoft Teams ({entry.title})",
            update_interval=timedelta(seconds=poll_interval),
        )
        self._previous_meeting_id: str | None = None
        self._sent_reminders: set[str] = set()
        self._event_listeners: list[Any] = []

    def register_event_listener(self, listener: Any) -> callback:
        """Register a callback for meeting lifecycle events."""
        self._event_listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._event_listeners:
                self._event_listeners.remove(listener)

        return _remove

    def _fire_meeting_event(self, event_type: str, event_data: dict[str, Any]) -> None:
        """Dispatch meeting lifecycle event to registered listeners."""
        for listener in list(self._event_listeners):
            try:
                listener(event_type, event_data)
            except Exception as err:
                _LOGGER.exception("Error dispatching meeting event: %s", err)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest data from Teams API and browser-daemon."""
        try:
            # 1. Fetch Presence
            presence = await self.client.get_presence()

            # 2. Fetch Upcoming Calendar Meetings
            now = datetime.now(UTC)
            meetings = await self.client.get_calendar_meetings(
                start_date=now - timedelta(minutes=30),
                end_date=now + timedelta(days=7),
                limit=15,
            )

            # 3. Fetch Active Call and Unread Chats Count
            active_call, unread_count = await self.client.get_active_call_and_unread()

            # Clear any previously registered auth issues
            ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self.entry.entry_id}")

        except TeamsAuthError as err:
            _LOGGER.error("Teams authentication failed: %s", err)
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"auth_failed_{self.entry.entry_id}",
                is_fixable=True,
                is_persistent=True,
                severity=ir.IssueSeverity.ERROR,
                translation_key="auth_expired",
                translation_placeholders={"name": self.entry.title},
                data={"entry_id": self.entry.entry_id},
            )
            raise ConfigEntryAuthFailed(err) from err
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Teams API: {err}") from err

        # Determine next meeting and calendar active meeting
        next_meeting: dict[str, Any] | None = None
        cal_active_meeting: dict[str, Any] | None = None

        for m in sorted(meetings, key=lambda x: x.get("start_time") or ""):
            start_dt = _parse_iso(m.get("start_time"))
            end_dt = _parse_iso(m.get("end_time"))
            if not start_dt or not end_dt:
                continue

            if start_dt <= now <= end_dt:
                cal_active_meeting = m
            elif start_dt > now and (next_meeting is None):
                next_meeting = m

            # Check 5-minute reminder
            meeting_id = m.get("id")
            if meeting_id and start_dt > now and (start_dt - now) <= timedelta(minutes=6):
                reminder_key = f"{meeting_id}_reminder"
                if reminder_key not in self._sent_reminders:
                    self._sent_reminders.add(reminder_key)
                    self._fire_meeting_event(
                        EVENT_MEETING_REMINDER,
                        {
                            "meeting_id": meeting_id,
                            "subject": m.get("subject"),
                            "start_time": m.get("start_time"),
                            "join_url": m.get("join_url"),
                        },
                    )

        # Merge live call awareness, presence, and calendar status
        is_in_meeting_by_presence = presence.get("activity") in (
            "InAMeeting",
            "InACall",
            "InAConferenceCall",
        ) or presence.get("availability") in ("InAMeeting", "InACall")

        active_meeting: dict[str, Any] | None = None

        if active_call:
            # Look for matching meeting on the calendar by thread_id, join_url, or subject
            matched_cal = None
            for m in meetings:
                if (
                    (m.get("thread_id") and m.get("thread_id") == active_call.get("thread_id"))
                    or (
                        m.get("join_url")
                        and active_call.get("url")
                        and m.get("join_url") == active_call.get("url")
                    )
                    or (
                        m.get("subject")
                        and active_call.get("title")
                        and m.get("subject").strip().lower()
                        == active_call.get("title").strip().lower()
                    )
                ):
                    matched_cal = m
                    break

            if matched_cal:
                active_meeting = dict(matched_cal)
                if not active_meeting.get("join_url") and active_call.get("url"):
                    active_meeting["join_url"] = active_call["url"]
            else:
                active_meeting = {
                    "id": active_call.get("thread_id"),
                    "subject": active_call.get("title"),
                    "join_url": active_call.get("url"),
                    "start_time": active_call.get("call_start_time"),
                    "meeting_code": active_call.get("meeting_code"),
                    "passcode": active_call.get("passcode"),
                    "is_online": True,
                }
        elif cal_active_meeting:
            active_meeting = cal_active_meeting
        elif is_in_meeting_by_presence:
            # If presence says InAMeeting or InACall, check if a calendar meeting is nearby
            # (started within 15 min early or ended within 60 min late)
            for m in meetings:
                s_dt = _parse_iso(m.get("start_time"))
                e_dt = _parse_iso(m.get("end_time"))
                if (
                    s_dt
                    and e_dt
                    and (s_dt - timedelta(minutes=15)) <= now <= (e_dt + timedelta(minutes=60))
                ):
                    active_meeting = m
                    break

        in_a_meeting = (
            (active_call is not None) or (active_meeting is not None) or is_in_meeting_by_presence
        )

        active_title = (
            active_meeting.get("subject")
            if active_meeting
            else (
                active_call.get("title")
                if active_call
                else ("In a Call" if is_in_meeting_by_presence else "")
            )
        )
        active_url = (
            active_meeting.get("join_url")
            if active_meeting
            else (active_call.get("url") if active_call else "")
        )

        current_meeting_id = (active_meeting.get("id") if active_meeting else None) or (
            active_title if in_a_meeting else None
        )

        # Trigger meeting lifecycle events
        if in_a_meeting and self._previous_meeting_id != current_meeting_id:
            self._previous_meeting_id = current_meeting_id
            self._fire_meeting_event(
                EVENT_MEETING_STARTED,
                {
                    "meeting_id": current_meeting_id,
                    "subject": active_title,
                    "url": active_url,
                },
            )
        elif not in_a_meeting and self._previous_meeting_id is not None:
            prev_id = self._previous_meeting_id
            self._previous_meeting_id = None
            self._fire_meeting_event(
                EVENT_MEETING_ENDED,
                {
                    "meeting_id": prev_id,
                },
            )

        return {
            "presence": presence,
            "meetings": meetings,
            "next_meeting": next_meeting,
            "active_meeting": active_meeting,
            "in_a_meeting": in_a_meeting,
            "meeting_title": active_title,
            "meeting_url": active_url,
            "unread_chats": unread_count,
            "user_mri": self.client.auth.user_mri,
            "user_display_name": self.client.auth.user_display_name,
            "user_email": self.client.auth.user_email,
        }
