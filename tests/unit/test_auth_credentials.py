"""Unit tests for app.auth.google credential helpers."""

import pytest
from google.oauth2.credentials import Credentials

from app.auth.constants import SCOPES
from app.auth.google import (
    CredentialsError,
    _read_google_client_from_env,
    load_google_credentials,
)
from app.auth.keyring_store import store_refresh_token


@pytest.mark.unit
def test_read_env_raises_when_client_id_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)

    with pytest.raises(CredentialsError, match="GOOGLE_OAUTH_CLIENT_ID"):
        _read_google_client_from_env()


@pytest.mark.unit
def test_read_env_raises_when_client_secret_missing(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_SECRET", raising=False)

    with pytest.raises(CredentialsError, match="GOOGLE_OAUTH_CLIENT_SECRET"):
        _read_google_client_from_env()


@pytest.mark.unit
def test_read_env_returns_both_values(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")

    client_id, client_secret = _read_google_client_from_env()
    assert client_id == "id-123"
    assert client_secret == "secret-456"


@pytest.mark.unit
def test_load_credentials_raises_when_keyring_empty(in_memory_keyring, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")

    with pytest.raises(CredentialsError, match="python -m app.auth --setup"):
        load_google_credentials()


@pytest.mark.unit
def test_load_credentials_returns_credentials_object(in_memory_keyring, monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "id-123")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret-456")
    store_refresh_token("tok-xyz")

    creds = load_google_credentials()

    assert isinstance(creds, Credentials)
    assert creds.refresh_token == "tok-xyz"
    assert creds.client_id == "id-123"
    assert creds.client_secret == "secret-456"
    assert list(creds.scopes) == SCOPES
