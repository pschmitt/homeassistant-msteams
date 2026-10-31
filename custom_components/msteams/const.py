"""Constants for the Microsoft Teams integration."""

from homeassistant.const import Platform

DOMAIN = "msteams"

# Configuration keys
CONF_AUTH_METHOD = "auth_method"
CONF_USE_BROWSER_DAEMON = "use_browser_daemon"
CONF_CREDENTIALS_FILE = "credentials_file"
CONF_DEFAULT_CONVERSATION_ID = "default_conversation_id"
CONF_POLL_INTERVAL = "poll_interval"
CONF_BROWSER_DAEMON_URL = "browser_daemon_url"
CONF_BROWSER_DAEMON_PROFILE = "browser_daemon_profile"
CONF_BROWSER_DAEMON_SECRET = "browser_daemon_secret"

# Auth methods
AUTH_METHOD_BROWSER_DAEMON = "browser_daemon"
AUTH_METHOD_CREDENTIALS_FILE = "credentials_file"
AUTH_METHOD_REFRESH_TOKEN = "refresh_token"

# Stored token state keys
CONF_SESSION_STATE = "session_state"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_TENANT_ID = "tenant_id"
CONF_CLIENT_ID = "client_id"
CONF_USER_MRI = "user_mri"
CONF_USER_DISPLAY_NAME = "user_display_name"
CONF_USER_EMAIL = "user_email"

# Defaults
DEFAULT_PROFILE = "work"
DEFAULT_BROWSER_DAEMON_URL = "https://browser.fnuc.ts.brkn.lol"
DEFAULT_POLL_INTERVAL = 30
DEFAULT_REGION = "emea"
DEFAULT_TEAMS_BASE_URL = "https://teams.microsoft.com/v2/"

# Teams OAuth / Client constants
TEAMS_CLIENT_ID = "5e3ce6c0-2b1f-4285-8d4b-75ee78787346"
TOKEN_ENDPOINT = "https://login.microsoftonline.com/{tenantId}/oauth2/v2.0/token"
AUTHSVC_ENDPOINT = "https://authsvc.teams.microsoft.com/v1.0/authz"

# Supported platforms
PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.NOTIFY,
    Platform.BUTTON,
]

# Event types for the Event entity
EVENT_MEETING_STARTED = "meeting_started"
EVENT_MEETING_ENDED = "meeting_ended"
EVENT_MEETING_REMINDER = "meeting_reminder"
EVENT_MESSAGE_RECEIVED = "message_received"
MEETING_EVENT_TYPES = [
    EVENT_MEETING_STARTED,
    EVENT_MEETING_ENDED,
    EVENT_MEETING_REMINDER,
]

# Reaction keys
REACTIONS = {
    "like": "like",
    "heart": "heart",
    "laugh": "laugh",
    "surprised": "surprised",
    "sad": "sad",
    "angry": "angry",
}
