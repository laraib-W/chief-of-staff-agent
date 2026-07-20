"""Plane credential management.

Public surface (used by app.nodes.fetch_plane and app.auth.__main__):
  - PlaneAuthError
  - read_plane_token_from_env
"""

import os


class PlaneAuthError(Exception):
    """Raised when the PLANE_API_TOKEN environment variable is missing.

    The message always names the variable and explains where to get it so
    it can be surfaced directly to the user without wrapping.
    """


def read_plane_token_from_env() -> str:
    """Read PLANE_API_TOKEN from the environment.

    Raises PlaneAuthError naming the missing variable and pointing to
    where it can be obtained.
    """
    token = os.environ.get("PLANE_API_TOKEN")
    if token is None:
        raise PlaneAuthError(
            "PLANE_API_TOKEN is not set. "
            "Generate a token at your Plane workspace → Settings → API Tokens, "
            "then add PLANE_API_TOKEN to your .env file."
        )
    if not token.strip():
        raise PlaneAuthError(
            "PLANE_API_TOKEN is empty. "
            "Generate a token at your Plane workspace → Settings → API Tokens, "
            "then set PLANE_API_TOKEN to a non-empty value in your .env file."
        )
    return token
