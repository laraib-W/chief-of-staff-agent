"""fetch_plane sensor node (specs.md §3.1.3).

Fetches issues for every project in config.plane.project_ids and maps them
to PlaneIssue objects. All HTTP calls are delegated to app.providers.plane.
"""

from datetime import UTC, date, datetime, timedelta

import structlog
from langgraph.types import RunnableConfig

from app.auth.plane import PlaneAuthError, read_plane_token_from_env
from app.config.loader import Config
from app.providers.plane import PlaneAPIError, PlaneClient
from app.schemas.digest import AgentState
from app.schemas.plane import PlaneIssue

log = structlog.get_logger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _age_in_state_days(state_updated_at: str | None) -> int:
    dt = _parse_dt(state_updated_at)
    if dt is None:
        return 0
    today = datetime.now(tz=UTC).date()
    return max(0, (today - dt.date()).days)


def _map_issue(
    raw: dict,
    project_id: str,
    project_identifier: str,
    state_lookup: dict[str, dict],
    member_lookup: dict[str, dict],
    module_lookup: dict[str, str],
) -> PlaneIssue:
    sequence_id = raw.get("sequence_id", 0)

    state_detail = state_lookup.get(raw.get("state", ""), {})

    assignee_ids = raw.get("assignees") or []
    first_assignee = member_lookup.get(assignee_ids[0]) if assignee_ids else None

    # Plane self-hosted uses "target_date"; cloud uses "due_date".
    due_date = _parse_date(raw.get("target_date") or raw.get("due_date"))
    completed_at = _parse_dt(raw.get("completed_at"))
    state_group = state_detail.get("group", "")
    today = datetime.now(tz=UTC).date()
    is_overdue = (
        due_date is not None
        and due_date < today
        and state_group not in ("completed", "cancelled")
    )

    return PlaneIssue(
        issue_id=f"{project_identifier}-{sequence_id}",
        project_id=project_id,
        title=raw.get("name", ""),
        state_name=state_detail.get("name", ""),
        state_group=state_group,
        assignee_id=first_assignee.get("id") if first_assignee else None,
        assignee_display_name=(
            first_assignee.get("display_name") if first_assignee else None
        ),
        module_name=module_lookup.get(raw.get("id", "")),
        due_date=due_date,
        created_at=_parse_dt(raw.get("created_at")),
        updated_at=_parse_dt(raw.get("updated_at")),
        completed_at=completed_at,
        age_in_state_days=_age_in_state_days(raw.get("state_updated_at")),
        is_overdue=is_overdue,
    )


def fetch_plane(state: AgentState, config: RunnableConfig) -> dict:
    cfg: Config = config["configurable"]["agent_config"]
    plane_cfg = cfg.plane
    issues: list[PlaneIssue] = []

    if not plane_cfg.workspace_slug:
        log.info("fetch_plane_skipped", reason="workspace_slug not configured")
        return {"plane_issues": []}

    try:
        token = read_plane_token_from_env()
        client = PlaneClient(base_url=plane_cfg.base_url, api_token=token)

        project_identifiers = {
            p["id"]: p.get("identifier", p["id"])
            for p in client.list_projects(plane_cfg.workspace_slug)
        }
        window_cutoff = datetime.now(tz=UTC) - timedelta(
            days=plane_cfg.rolling_window_days
        )

        members = client.list_members(plane_cfg.workspace_slug)
        member_lookup: dict[str, dict] = {m["id"]: m for m in members}

        for project_id in plane_cfg.project_ids:
            states = client.get_states(plane_cfg.workspace_slug, project_id)
            state_lookup: dict[str, dict] = {s["id"]: s for s in states}

            modules = client.list_modules(plane_cfg.workspace_slug, project_id)
            module_lookup: dict[str, str] = {}
            for module in modules:
                for issue in client.list_module_issues(
                    plane_cfg.workspace_slug, project_id, module["id"]
                ):
                    module_lookup[issue["id"]] = module["name"]

            def _should_stop(
                page_results: list[dict], _sl: dict = state_lookup
            ) -> bool:
                """Stop only when every page is entirely old closed issues.

                Active issues are always fetched regardless of last update date.
                Only once a full page is entirely old closed issues can we safely
                stop — all later pages will be older still.
                """
                if not page_results:
                    return True
                for raw in page_results:
                    state_group = _sl.get(raw.get("state", ""), {}).get("group", "")
                    if state_group not in ("completed", "cancelled"):
                        return False
                dates = [_parse_dt(raw.get("updated_at")) for raw in page_results]
                valid = [d for d in dates if d is not None]
                return bool(valid) and min(valid) < window_cutoff

            raw_issues = client.get_issues(
                plane_cfg.workspace_slug,
                project_id,
                order_by="-updated_at",
                stop_early=_should_stop,
            )
            project_identifier = project_identifiers.get(project_id)
            if project_identifier is None:
                log.warning("project_not_in_workspace", project_id=project_id)
                project_identifier = project_id

            for raw in raw_issues:
                completed_at = _parse_dt(raw.get("completed_at"))
                state_group = state_lookup.get(raw.get("state", ""), {}).get(
                    "group", ""
                )
                # Drop old closed issues that slipped through the stop condition.
                if state_group in ("completed", "cancelled") and (
                    completed_at is None or completed_at < window_cutoff
                ):
                    continue

                assignee_ids = raw.get("assignees") or []
                assignee_names = [
                    member_lookup.get(uid, {}).get("display_name", "")
                    for uid in assignee_ids
                ]
                if any(name in plane_cfg.ignore_list for name in assignee_names):
                    continue

                if not raw.get("created_at") or not raw.get("updated_at"):
                    log.warning(
                        "issue_missing_timestamps",
                        sequence_id=raw.get("sequence_id"),
                    )
                    continue

                issues.append(
                    _map_issue(
                        raw,
                        project_id,
                        project_identifier,
                        state_lookup,
                        member_lookup,
                        module_lookup,
                    )
                )

        log.info("fetch_plane_done", total_issues=len(issues))
        return {"plane_issues": issues}

    except (PlaneAuthError, PlaneAPIError) as exc:
        log.error("fetch_plane_failed", reason=str(exc))
        return {
            "plane_issues": [],
            "errors": {**state.get("errors", {}), "plane": str(exc)},
        }
