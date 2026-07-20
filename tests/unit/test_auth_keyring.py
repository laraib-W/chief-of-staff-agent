"""Unit tests for app.auth.keyring_store."""

import pytest

from app.auth.google import CredentialsError
from app.auth.keyring_store import (
    clear_refresh_token,
    load_refresh_token,
    store_refresh_token,
)


@pytest.mark.unit
def test_store_and_load_roundtrip(in_memory_keyring):
    store_refresh_token("tok-abc")
    assert load_refresh_token() == "tok-abc"


@pytest.mark.unit
def test_load_returns_none_when_empty(in_memory_keyring):
    assert load_refresh_token() is None


@pytest.mark.unit
def test_store_overwrites_existing(in_memory_keyring):
    store_refresh_token("tok-old")
    store_refresh_token("tok-new")
    assert load_refresh_token() == "tok-new"


@pytest.mark.unit
def test_clear_removes_token(in_memory_keyring):
    store_refresh_token("tok-abc")
    clear_refresh_token()
    assert load_refresh_token() is None


@pytest.mark.unit
def test_clear_is_idempotent(in_memory_keyring):
    """clear_refresh_token must not raise when nothing is stored."""
    clear_refresh_token()
    clear_refresh_token()


@pytest.mark.unit
def test_ensure_backend_available_passes_for_memory_keyring(in_memory_keyring):
    from app.auth.keyring_store import ensure_backend_available

    ensure_backend_available()  # _MemoryKeyring is not a fail/null backend


@pytest.mark.unit
def test_ensure_backend_raises_for_fail_backend(monkeypatch):
    import keyring

    monkeypatch.setattr(keyring, "get_keyring", lambda: keyring.backends.fail.Keyring())

    from app.auth.keyring_store import ensure_backend_available

    with pytest.raises(CredentialsError, match="No OS keyring backend"):
        ensure_backend_available()
