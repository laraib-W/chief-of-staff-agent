"""Auth constants — scopes, keyring coordinates, Google OAuth endpoints."""

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
]

KEYRING_SERVICE = "chief-of-staff-agent"
KEYRING_USERNAME = "google_oauth_refresh_token"

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105
