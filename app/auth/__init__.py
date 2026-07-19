"""Google OAuth credential management for the Chief-of-Staff Agent.

Public API — the only symbols Phase 1+ providers should import:

  from app.auth import SCOPES, CredentialsError, load_google_credentials

Everything else in this package is internal.
"""

from app.auth.constants import SCOPES
from app.auth.google import CredentialsError, load_google_credentials

__all__ = ["SCOPES", "CredentialsError", "load_google_credentials"]
