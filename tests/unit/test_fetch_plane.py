"""Unit tests for the fetch_plane node and the --list-projects CLI command."""

import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock

import pytest

from app.auth.__main__ import main
from app.auth.plane import PlaneAuthError, read_plane_token_from_env
from app.nodes.fetch_plane import fetch_plane
from app.providers.plane import PlaneAPIError
from app.schemas.plane import PlaneIssue

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_PROJECT_ID = "PROJ-abc-123"
_DEFAULT_IDENTIFIER = "PROJ"
_NODE = "app.nodes.fetch_plane"
_AUTH_MAIN = "app.auth.__main__"

# Fake state UUIDs used in raw issue fixtures.
_STATE_UUID: dict[str, str] = {
    "backlog": "state-uuid-backlog",
    "unstarted": "state-uuid-unstarted",
    "started": "state-uuid-started",
    "completed": "state-uuid-completed",
    "cancelled": "state-uuid-cancelled",
}

# Full state objects returned by get_states mock.
_STATES_LIST: list[dict] = [
    {"id": uuid, "name": group.title(), "group": group}
    for group, uuid in _STATE_UUID.items()
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_raw_issue(
    sequence_id: int = 1,
    name: str = "Fix bug",
    state_group: str = "started",
    state_name: str = "In Progress",
    assignee_id: str = "user-1",
    assignee_name: str = "Alice",
    due_date: str | None = None,
    completed_at: str | None = None,
    state_updated_at: str = "2026-07-15T00:00:00Z",
) -> dict:
    """Build a raw issue dict matching the actual Plane v1 API response shape.

    The API never expands state_detail or assignee_details; it returns a state
    UUID and a list of assignee UUIDs instead.
    """
    return {
        "id": f"uuid-{sequence_id}",
        "sequence_id": sequence_id,
        "name": name,
        "state": _STATE_UUID.get(state_group, _STATE_UUID["started"]),
        "assignees": [assignee_id] if assignee_id else [],
        "target_date": due_date,
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-15T00:00:00Z",
        "completed_at": completed_at,
        "state_updated_at": state_updated_at,
    }


def _make_config(
    project_ids: list[str] | None = None,
    ignore_list: list[str] | None = None,
    rolling_window_days: int = 7,
) -> MagicMock:
    cfg = MagicMock()
    cfg.plane.base_url = "https://api.plane.so"
    cfg.plane.workspace_slug = "test-workspace"
    cfg.plane.project_ids = project_ids or [_DEFAULT_PROJECT_ID]
    cfg.plane.ignore_list = ignore_list or []
    cfg.plane.rolling_window_days = rolling_window_days
    return cfg


def _make_mock_client(
    active_issues: list[dict],
    closed_issues: list[dict] | None = None,
    project_id: str = _DEFAULT_PROJECT_ID,
) -> MagicMock:
    """Return a PlaneClient mock with all methods used by fetch_plane set up.

    get_issues returns active_issues + closed_issues as one combined list.
    get_states returns the shared _STATES_LIST fixture.
    list_members provides Alice (user-1) and Bob (user-2) by default.
    """
    mock = MagicMock()
    mock.list_projects.return_value = [
        {"id": project_id, "identifier": _DEFAULT_IDENTIFIER}
    ]
    mock.get_issues.return_value = active_issues + (closed_issues or [])
    mock.get_states.return_value = _STATES_LIST
    mock.list_members.return_value = [
        {"id": "user-1", "display_name": "Alice"},
        {"id": "user-2", "display_name": "Bob"},
    ]
    mock.list_modules.return_value = []
    mock.list_module_issues.return_value = []
    return mock


def _langgraph_config(agent_config: MagicMock) -> dict:
    return {"configurable": {"thread_id": "test", "agent_config": agent_config}}


def _run_cli(args: list[str], monkeypatch) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["python -m app.auth", *args])
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    exit_code = 0
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        try:
            main()
        except SystemExit as exc:
            exit_code = exc.code if isinstance(exc.code, int) else 1
    return exit_code, stdout_buf.getvalue(), stderr_buf.getvalue()


def _mock_load_config(plane_cfg: MagicMock):
    """Return a load_config stub that wraps plane_cfg in a config object."""

    def _inner(_path):
        cfg = MagicMock()
        cfg.plane = plane_cfg
        return cfg

    return _inner


def _raise_plane_auth_error():
    raise PlaneAuthError("PLANE_API_TOKEN is not set")


# ---------------------------------------------------------------------------
# read_plane_token_from_env unit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_token_missing_raises(monkeypatch):
    """Missing PLANE_API_TOKEN raises PlaneAuthError with 'not set' message."""
    monkeypatch.delenv("PLANE_API_TOKEN", raising=False)
    with pytest.raises(PlaneAuthError, match="not set"):
        read_plane_token_from_env()


@pytest.mark.unit
def test_read_token_empty_raises(monkeypatch):
    """Empty PLANE_API_TOKEN raises PlaneAuthError with 'empty' message."""
    monkeypatch.setenv("PLANE_API_TOKEN", "")
    with pytest.raises(PlaneAuthError, match="empty"):
        read_plane_token_from_env()


@pytest.mark.unit
def test_read_token_whitespace_only_raises(monkeypatch):
    """Whitespace-only PLANE_API_TOKEN is treated as empty."""
    monkeypatch.setenv("PLANE_API_TOKEN", "   ")
    with pytest.raises(PlaneAuthError, match="empty"):
        read_plane_token_from_env()


@pytest.mark.unit
def test_read_token_valid_returns_token(monkeypatch):
    """A valid token is returned as-is."""
    monkeypatch.setenv("PLANE_API_TOKEN", "tok-abc-123")
    assert read_plane_token_from_env() == "tok-abc-123"


# ---------------------------------------------------------------------------
# fetch_plane node tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_happy_path_returns_issues(monkeypatch):
    """Node returns PlaneIssue objects when the API responds successfully."""
    mock_client = _make_mock_client(
        [_make_raw_issue(1, "Bug A"), _make_raw_issue(2, "Bug B")]
    )
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    assert "plane_issues" in result
    assert len(result["plane_issues"]) == 2
    assert all(isinstance(i, PlaneIssue) for i in result["plane_issues"])
    assert result["plane_issues"][0].title == "Bug A"
    assert result.get("errors", {}).get("plane") is None


@pytest.mark.unit
def test_happy_path_uses_api_identifier(monkeypatch):
    """issue_id comes from list_projects identifier, not the config UUID."""
    mock_client = _make_mock_client([_make_raw_issue(7, "Some task")])
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    assert result["plane_issues"][0].issue_id == f"{_DEFAULT_IDENTIFIER}-7"


@pytest.mark.unit
def test_unknown_project_id_falls_back_to_uuid_with_warning(monkeypatch, capsys):
    """If project_id isn't in the workspace, issue_id falls back to the UUID."""
    mock_client = _make_mock_client([_make_raw_issue(1, "Task")])
    mock_client.list_projects.return_value = []  # workspace returns no projects
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    assert result["plane_issues"][0].issue_id == f"{_DEFAULT_PROJECT_ID}-1"
    # structlog writes to stdout; verify the warning event was emitted
    assert "project_not_in_workspace" in capsys.readouterr().out


@pytest.mark.unit
def test_token_failure_writes_error_and_returns_empty(monkeypatch):
    """PlaneAuthError from the auth layer maps to errors['plane'] and an empty list."""
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", _raise_plane_auth_error)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    assert result["plane_issues"] == []
    assert "PLANE_API_TOKEN" in result["errors"]["plane"]


@pytest.mark.unit
def test_workspace_slug_empty_skips_silently(monkeypatch):
    """Empty workspace_slug returns empty issues with no error and no API calls."""
    mock_client = _make_mock_client([])
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    cfg = _make_config()
    cfg.plane.workspace_slug = ""
    result = fetch_plane({"errors": {}}, _langgraph_config(cfg))

    assert result == {"plane_issues": []}
    mock_client.list_projects.assert_not_called()
    mock_client.get_issues.assert_not_called()


@pytest.mark.unit
def test_empty_project_returns_empty_list(monkeypatch):
    """An empty project produces an empty plane_issues list without an error."""
    mock_client = _make_mock_client([])
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    assert result["plane_issues"] == []
    assert result.get("errors", {}).get("plane") is None


@pytest.mark.unit
def test_ignore_list_excludes_assignee(monkeypatch):
    """Issues belonging to an ignored assignee are dropped from the output."""
    mock_client = _make_mock_client(
        [
            _make_raw_issue(1, "Task A", assignee_id="user-1", assignee_name="Alice"),
            _make_raw_issue(2, "Task B", assignee_id="user-2", assignee_name="Bob"),
        ]
    )
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane(
        {"errors": {}}, _langgraph_config(_make_config(ignore_list=["Bob"]))
    )

    titles = [i.title for i in result["plane_issues"]]
    assert "Task A" in titles
    assert "Task B" not in titles


@pytest.mark.unit
def test_overdue_flag_set_correctly(monkeypatch):
    """is_overdue is True for past due dates on non-completed issues."""
    mock_client = _make_mock_client(
        active_issues=[
            _make_raw_issue(
                1, "Overdue task", due_date="2020-01-01", state_group="started"
            ),
            _make_raw_issue(
                2, "Future task", due_date="2099-12-31", state_group="started"
            ),
        ],
        closed_issues=[
            _make_raw_issue(
                3,
                "Done task",
                due_date="2020-01-01",
                state_group="completed",
                completed_at="2026-07-19T00:00:00Z",
            ),
        ],
    )
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    by_title = {i.title: i for i in result["plane_issues"]}
    assert by_title["Overdue task"].is_overdue is True
    assert by_title["Future task"].is_overdue is False
    assert by_title["Done task"].is_overdue is False


@pytest.mark.unit
def test_rolling_window_excludes_completed_with_no_timestamp(monkeypatch):
    """Completed/cancelled issues with no completed_at are dropped."""
    mock_client = _make_mock_client(
        active_issues=[
            _make_raw_issue(3, "Active", state_group="started", completed_at=None),
        ],
        closed_issues=[
            _make_raw_issue(
                1, "No timestamp done", state_group="completed", completed_at=None
            ),
            _make_raw_issue(
                2, "No timestamp cancelled", state_group="cancelled", completed_at=None
            ),
        ],
    )
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane({"errors": {}}, _langgraph_config(_make_config()))

    titles = {i.title for i in result["plane_issues"]}
    assert "Active" in titles
    assert "No timestamp done" not in titles
    assert "No timestamp cancelled" not in titles


@pytest.mark.unit
def test_rolling_window_excludes_old_completed(monkeypatch):
    """Python-side guard drops closed issues with completed_at before the window.

    The server filter reduces traffic, but the Python guard is the authoritative
    check — this test verifies it still works even when old issues slip through.
    """
    mock_client = _make_mock_client(
        active_issues=[
            _make_raw_issue(
                4, "Active issue", state_group="started", completed_at=None
            ),
        ],
        closed_issues=[
            _make_raw_issue(
                1,
                "Recent done",
                state_group="completed",
                completed_at="2026-07-19T00:00:00Z",
            ),
            _make_raw_issue(
                2,
                "Old done",
                state_group="completed",
                completed_at="2020-01-01T00:00:00Z",
            ),
            _make_raw_issue(
                3,
                "Old cancelled",
                state_group="cancelled",
                completed_at="2020-01-01T00:00:00Z",
            ),
        ],
    )
    monkeypatch.setattr(f"{_NODE}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_NODE}.PlaneClient", lambda **_: mock_client)

    result = fetch_plane(
        {"errors": {}}, _langgraph_config(_make_config(rolling_window_days=7))
    )

    titles = {i.title for i in result["plane_issues"]}
    assert "Recent done" in titles
    assert "Active issue" in titles
    assert "Old done" not in titles
    assert "Old cancelled" not in titles


# ---------------------------------------------------------------------------
# --list-projects CLI tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_projects_prints_name_and_id(monkeypatch):
    """--list-projects prints each project's name and ID."""
    plane_cfg = MagicMock()
    plane_cfg.base_url = "https://api.plane.so"
    plane_cfg.workspace_slug = "test-ws"

    mock_client = MagicMock()
    mock_client.list_projects.return_value = [
        {"name": "Alpha", "id": "id-001"},
        {"name": "Beta", "id": "id-002"},
    ]

    monkeypatch.setattr(f"{_AUTH_MAIN}.load_dotenv", lambda: None)
    monkeypatch.setattr(f"{_AUTH_MAIN}.load_config", _mock_load_config(plane_cfg))
    monkeypatch.setattr(f"{_AUTH_MAIN}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_AUTH_MAIN}.PlaneClient", lambda **_: mock_client)

    code, out, _ = _run_cli(["--list-projects"], monkeypatch)

    assert code == 0
    assert "Alpha" in out
    assert "id-001" in out
    assert "Beta" in out
    assert "id-002" in out


@pytest.mark.unit
def test_list_projects_exits_2_on_missing_token(monkeypatch):
    """--list-projects exits 2 when PLANE_API_TOKEN is missing."""
    plane_cfg = MagicMock()
    plane_cfg.base_url = "https://api.plane.so"
    plane_cfg.workspace_slug = "test-ws"

    monkeypatch.setattr(f"{_AUTH_MAIN}.load_dotenv", lambda: None)
    monkeypatch.setattr(f"{_AUTH_MAIN}.load_config", _mock_load_config(plane_cfg))
    monkeypatch.setattr(
        f"{_AUTH_MAIN}.read_plane_token_from_env", _raise_plane_auth_error
    )

    code, _, err = _run_cli(["--list-projects"], monkeypatch)

    assert code == 2
    assert "PLANE_API_TOKEN" in err


@pytest.mark.unit
def test_list_projects_exits_2_on_api_error(monkeypatch):
    """--list-projects exits 2 when the Plane API call fails."""
    plane_cfg = MagicMock()
    plane_cfg.base_url = "https://api.plane.so"
    plane_cfg.workspace_slug = "test-ws"

    mock_client = MagicMock()
    mock_client.list_projects.side_effect = PlaneAPIError("403 Forbidden")

    monkeypatch.setattr(f"{_AUTH_MAIN}.load_dotenv", lambda: None)
    monkeypatch.setattr(f"{_AUTH_MAIN}.load_config", _mock_load_config(plane_cfg))
    monkeypatch.setattr(f"{_AUTH_MAIN}.read_plane_token_from_env", lambda: "tok")
    monkeypatch.setattr(f"{_AUTH_MAIN}.PlaneClient", lambda **_: mock_client)

    code, _, err = _run_cli(["--list-projects"], monkeypatch)

    assert code == 2
    assert "403" in err
