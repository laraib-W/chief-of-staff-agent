"""Unit tests for the app.auth CLI (app/auth/__main__.py)."""

import sys

import pytest

from app.auth.google import CredentialsError
from app.auth.keyring_store import load_refresh_token, store_refresh_token


def _run_cli(args: list[str], monkeypatch) -> tuple[int, str, str]:
    """Invoke the CLI's main() function, capture stdout/stderr, and return exit code."""
    import io

    from app.auth.__main__ import main

    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout_capture)
    monkeypatch.setattr(sys, "stderr", stderr_capture)
    monkeypatch.setattr(sys, "argv", ["python -m app.auth", *args])

    exit_code = 0
    try:
        main()
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else 1

    return exit_code, stdout_capture.getvalue(), stderr_capture.getvalue()


@pytest.mark.unit
def test_setup_already_authenticated_skips_flow(in_memory_keyring, monkeypatch):
    """--setup should short-circuit without invoking the OAuth flow."""
    store_refresh_token("existing-token")

    flow_called = []

    def _sentinel(_client_id, _client_secret):
        flow_called.append(True)
        raise AssertionError("run_setup_flow must not be called")

    monkeypatch.setattr("app.auth.__main__.run_setup_flow", _sentinel)
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setattr("app.auth.__main__.load_dotenv", lambda: None)

    code, out, _ = _run_cli(["--setup"], monkeypatch)

    assert code == 0
    assert "already authenticated" in out.lower()
    assert not flow_called


@pytest.mark.unit
def test_setup_happy_path_stores_token(in_memory_keyring, monkeypatch):
    """--setup should call the flow and store the returned token."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setattr("app.auth.__main__.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "app.auth.__main__.run_setup_flow",
        lambda _cid, _csec: "fresh-token",
    )

    code, out, _ = _run_cli(["--setup"], monkeypatch)

    assert code == 0
    assert load_refresh_token() == "fresh-token"
    assert "stored" in out.lower()


@pytest.mark.unit
def test_reauth_overwrites_existing_token(in_memory_keyring, monkeypatch):
    """--reauth should replace the existing token without pre-clearing."""
    store_refresh_token("old-token")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setattr("app.auth.__main__.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "app.auth.__main__.run_setup_flow",
        lambda _cid, _csec: "new-token",
    )

    code, _, _ = _run_cli(["--reauth"], monkeypatch)

    assert code == 0
    assert load_refresh_token() == "new-token"


@pytest.mark.unit
def test_setup_exits_2_on_missing_env(in_memory_keyring, monkeypatch):
    """CLI must exit 2 with an actionable message when env vars are absent."""
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("app.auth.__main__.load_dotenv", lambda: None)

    code, _, err = _run_cli(["--setup"], monkeypatch)

    assert code == 2
    assert "GOOGLE_OAUTH_CLIENT_ID" in err


@pytest.mark.unit
def test_setup_exits_2_when_credentials_error_raised(in_memory_keyring, monkeypatch):
    """Any CredentialsError from the flow must map to exit code 2."""
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    monkeypatch.setattr("app.auth.__main__.load_dotenv", lambda: None)
    monkeypatch.setattr(
        "app.auth.__main__.run_setup_flow",
        lambda _cid, _csec: (_ for _ in ()).throw(
            CredentialsError("OAuth client must be type Desktop app")
        ),
    )

    code, _, err = _run_cli(["--setup"], monkeypatch)

    assert code == 2
    assert "Desktop app" in err
