"""DataUpdateCoordinator for Microsoft Teams integration."""

from __future__ import annotations

import asyncio
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
from .trouter import TeamsTrouter

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
        self._push_refresh_task: asyncio.Task | None = None
        self.trouter = TeamsTrouter(
            client.auth,
            self._schedule_push_refresh,
            lambda coro: entry.async_create_background_task(
                hass, coro, f"{DOMAIN}_{entry.entry_id}_push"
            ),
        )

        # Persist rotated tokens to config entry whenever they refresh
        self.client.auth.on_tokens_refreshed = self._handle_tokens_refreshed

    @callback
    def _schedule_push_refresh(self) -> None:
        """Coalesce bursts of push events into a single refresh."""
        if self._push_refresh_task is None or self._push_refresh_task.done():
            self._push_refresh_task = self.hass.async_create_task(self._refresh_after_push())

    async def _refresh_after_push(self) -> None:
        await asyncio.sleep(2)
        await self.async_request_refresh()

    async def async_stop_push(self) -> None:
        await self.trouter.stop()
        if self._push_refresh_task:
            self._push_refresh_task.cancel()
            await asyncio.gather(self._push_refresh_task, return_exceptions=True)

    @callback
    def _handle_tokens_refreshed(self, exported: dict[str, Any]) -> None:
        """Persist refreshed tokens back into ConfigEntry data."""
        if not self.hass or not self.entry:
            return
        new_data = {
            **self.entry.data,
            **exported,
        }
        _LOGGER.debug("Persisting rotated Microsoft Teams tokens to config entry")
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)

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
            ir.async_delete_issue(
                self.hass, "homeassistant", f"config_entry_reauth_{self.entry.entry_id}"
            )

        except TeamsAuthError as err:
            _LOGGER.warning(
                "Teams authentication failed (%s), attempting automatic recovery via browser-daemon...",
                err,
            )
            recovered = False
            try:
                recovered = await self.client.auth._async_recover_via_browser_daemon()
            except Exception as rec_err:
                _LOGGER.warning("Automatic recovery via browser-daemon raised error: %s", rec_err)

            if recovered:
                try:
                    presence = await self.client.get_presence()
                    now = datetime.now(UTC)
                    meetings = await self.client.get_calendar_meetings(
                        start_date=now - timedelta(minutes=30),
                        end_date=now + timedelta(days=7),
                        limit=15,
                    )
                    active_call, unread_count = await self.client.get_active_call_and_unread()
                    ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self.entry.entry_id}")
                    ir.async_delete_issue(
                        self.hass, "homeassistant", f"config_entry_reauth_{self.entry.entry_id}"
                    )
                except Exception as retry_err:
                    _LOGGER.error("Data fetch failed after token recovery: %s", retry_err)
                    raise UpdateFailed(
                        f"Error communicating with Teams API: {retry_err}"
                    ) from retry_err
            else:
                _LOGGER.error("Teams authentication failed and recovery unsuccessful: %s", err)
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
                    "thread_id": active_call.get("thread_id"),
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

        # Resolve participants for next meeting and active meeting
        if next_meeting and next_meeting.get("thread_id"):
            try:
                next_meeting["participants"] = await self.client.get_thread_participants(
                    next_meeting["thread_id"]
                )
                next_meeting["participants_count"] = len(next_meeting["participants"])
            except Exception as err:
                _LOGGER.debug("Failed to resolve participants for next meeting: %s", err)

        if active_meeting and active_meeting.get("thread_id"):
            try:
                active_meeting["participants"] = await self.client.get_thread_participants(
                    active_meeting["thread_id"]
                )
                active_meeting["participants_count"] = len(active_meeting["participants"])
            except Exception as err:
                _LOGGER.debug("Failed to resolve participants for active meeting: %s", err)

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
