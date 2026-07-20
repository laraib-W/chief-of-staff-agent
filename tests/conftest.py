"""Shared pytest fixtures for all test tiers."""

import json
from pathlib import Path

import keyring
import keyring.backend
import pytest

from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def make_config(**overrides) -> Config:
    defaults = {
        "identity": IdentityConfig(
            user_name="T", timezone="UTC", delivery_address="t@x.com"
        ),
        "gmail": GmailConfig(),
        "plane": PlaneConfig(project_ids=["p1"]),
        "thresholds": ThresholdsConfig(),
        "llm": LLMConfig(),
        "config_hash": "deadbeef",
    }
    defaults.update(overrides)
    return Config(**defaults)


def load_gmail_fixture(name: str = "gmail_sample") -> list[dict]:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


class _MemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring backend for tests — no OS interaction."""

    priority = 999  # highest priority so it wins during tests

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        import keyring.errors

        if (service, username) not in self._store:
            raise keyring.errors.PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture
def in_memory_keyring():
    """Swap in a fresh in-memory keyring for the duration of a test.

    Restores the original backend on teardown so tests are fully isolated.
    """
    original = keyring.get_keyring()
    mem = _MemoryKeyring()
    keyring.set_keyring(mem)
    yield mem
    keyring.set_keyring(original)
