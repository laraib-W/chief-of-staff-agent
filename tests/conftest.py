"""Shared pytest fixtures for all test tiers."""

import json
from datetime import UTC, date, datetime, timedelta
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
from app.schemas.plane import PlaneIssue

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


def make_plane_issue(
    issue_id: str = "PROJ-1",
    *,
    project_id: str = "p1",
    title: str = "Task",
    state_group: str = "started",
    state_name: str = "In Progress",
    assignee_ids: list[str] | None = None,
    assignee_display_names: list[str] | None = None,
    due_date: date | None = None,
    completed_at: datetime | None = None,
    age_in_state_days: int = 0,
    is_overdue: bool = False,
    module_name: str | None = None,
    updated_at: datetime | None = None,
    created_at: datetime | None = None,
) -> PlaneIssue:
    """Build a fully-formed PlaneIssue for assess_team tests.

    Defaults model an active 'started' issue with a single assignee
    ("user-1" / "Alice"), no due date, and no age. Override the assignee
    lists to model co-assigned work or (empty lists) unassigned issues.
    """
    now = datetime.now(tz=UTC)
    if assignee_ids is None:
        assignee_ids = ["user-1"]
    if assignee_display_names is None:
        assignee_display_names = ["Alice"] if assignee_ids == ["user-1"] else []
    return PlaneIssue(
        issue_id=issue_id,
        project_id=project_id,
        title=title,
        state_name=state_name,
        state_group=state_group,
        assignee_ids=assignee_ids,
        assignee_display_names=assignee_display_names,
        due_date=due_date,
        created_at=created_at or (now - timedelta(days=30)),
        updated_at=updated_at or now,
        completed_at=completed_at,
        module_name=module_name,
        age_in_state_days=age_in_state_days,
        is_overdue=is_overdue,
    )


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
