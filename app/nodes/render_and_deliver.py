"""render_and_deliver node — Jinja render + email send (specs.md §3.4.1).

Tail of the graph. Composes a single HTML digest from whatever state the
sensor + judgment nodes produced, sends it to ``identity.delivery_address``
via the Gmail API, and archives the delivered HTML to
``memory.db.digest_log`` on success. Partial-failure banner (specs §9) is
computed from ``state["errors"]`` so a missing sensor never silently drops a
section.

The ``sender`` callable is injectable so tests can bypass Gmail entirely.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import structlog
from jinja2 import Environment, PackageLoader, select_autoescape
from langgraph.types import RunnableConfig

from app.config.loader import Config
from app.providers.gmail import GmailClient
from app.schemas.digest import AgentState
from app.schemas.plane import PERSON_STATUS_ORDER
from app.storage.memory import log_digest

log = structlog.get_logger(__name__)

_SUBJECT_FMT = "Morning digest — {today}"
_FAILURE_LABELS = {
    "gmail": "Gmail",
    "calendar": "Calendar",
    "plane": "Plane",
}

Sender = Callable[[str, str], tuple[str | None, str | None]]


def render_and_deliver_node(
    agent_config: Config,
    sender: Sender | None = None,
) -> Callable[[AgentState, RunnableConfig], dict]:
    """Build the render_and_deliver node bound to a loaded Config.

    ``sender`` maps ``(subject, html_body) -> (message_id, error)``. When
    omitted, sends via ``GmailClient.send_html`` to
    ``identity.delivery_address``; tests inject a fake.
    """
    identity = agent_config.identity
    tz = ZoneInfo(identity.timezone)
    env = Environment(
        loader=PackageLoader("app", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("digest.html.j2")

    default_sender = _build_default_sender(agent_config, identity.delivery_address)
    send = sender or default_sender

    def _node(state: AgentState, config: RunnableConfig) -> dict:
        errors = state.get("errors") or {}
        today_local = datetime.now(tz=tz).date()
        today_label = today_local.strftime("%A, %d %B %Y")
        subject = _SUBJECT_FMT.format(today=today_local.isoformat())

        context = _build_context(
            state=state,
            errors=errors,
            user_name=identity.user_name,
            today_label=today_label,
            tz=tz,
        )
        digest_html = template.render(**context)

        configurable = config.get("configurable") or {}
        dry_run: bool = bool(configurable.get("dry_run", False))

        if dry_run:
            log.info("render_and_deliver.dry_run", bytes=len(digest_html))
            return {"digest_html": digest_html}

        message_id, send_error = send(subject, digest_html)
        if send_error:
            errors = {**errors, "delivery": send_error}
            log.warning("render_and_deliver.send_error", error=send_error)
            return {"digest_html": digest_html, "errors": errors}

        memory_db_path: Path | None = configurable.get("memory_db_path")
        run_id = configurable.get("run_id")
        if memory_db_path is not None and run_id is not None:
            log_digest(memory_db_path, run_id=int(run_id), digest_html=digest_html)

        log.info(
            "render_and_deliver.ok",
            message_id=message_id,
            bytes=len(digest_html),
        )
        return {"digest_html": digest_html}

    return _node


def _build_default_sender(agent_config: Config, to: str) -> Sender:
    """Return a Sender bound to a fresh GmailClient targeting ``to``."""
    client = GmailClient(agent_config.gmail)

    def _send(subject: str, html_body: str) -> tuple[str | None, str | None]:
        return client.send_html(to=to, subject=subject, html_body=html_body)

    return _send


def _build_context(
    *,
    state: AgentState,
    errors: dict[str, str | None],
    user_name: str,
    today_label: str,
    tz: ZoneInfo,
) -> dict:
    today_events = _today_events_view(state.get("calendar_events") or [], tz)
    needs_reply, classification_pending = _needs_reply_view(state)
    team_cards = _team_cards_view(state.get("team_health") or [])
    priorities = state.get("top_priorities") or []
    raw_email_count = len(state.get("emails") or [])

    return {
        "user_name": user_name,
        "today_label": today_label,
        "errors": errors,
        "failure_banner": _failure_banner(errors),
        "today_events": today_events,
        "needs_reply": needs_reply,
        "email_classification_pending": classification_pending,
        "raw_email_count": raw_email_count,
        "team_cards": team_cards,
        "priorities": priorities,
    }


def _failure_banner(errors: dict[str, str | None]) -> str | None:
    """One line naming every source that errored on this run.

    Sources are listed in the canonical order defined by ``_FAILURE_LABELS``
    so the banner reads the same regardless of which sensor finished first.
    """
    order = list(_FAILURE_LABELS)
    failed_keys = sorted(
        (k for k, v in errors.items() if v),
        key=lambda k: (order.index(k) if k in order else len(order), k),
    )
    if not failed_keys:
        return None
    failed = [_FAILURE_LABELS.get(k, k) for k in failed_keys]
    if len(failed) == 1:
        return f"{failed[0]} data unavailable today."
    return f"{', '.join(failed[:-1])} and {failed[-1]} data unavailable today."


def _today_events_view(events: list, tz: ZoneInfo) -> list[dict]:
    """Convert today-scoped CalendarEvent objects into template-friendly dicts."""
    today = datetime.now(tz=tz).date()
    view = []
    for ev in events:
        if ev.scope != "today":
            continue
        start_local = _to_local(ev.start, tz)
        if start_local.date() != today:
            continue
        if ev.all_day:
            when = "All day"
        else:
            end_local = _to_local(ev.end, tz)
            when = f"{start_local.strftime('%H:%M')}–{end_local.strftime('%H:%M')}"
        view.append(
            {
                "summary": ev.summary,
                "when": when,
                "location": ev.location,
                "is_pending_invite": ev.is_pending_invite,
                "_sort": start_local,
            }
        )
    view.sort(key=lambda x: x["_sort"])
    for row in view:
        row.pop("_sort")
    return view


def _to_local(value: datetime, tz: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz)


def _needs_reply_view(state: AgentState) -> tuple[list[dict], bool]:
    """Return (rows, classification_pending).

    Until ``classify_emails`` lands, ``email_actions`` is always empty and
    every raw email lands in the classification-pending fallback. The dev
    who wires ``classify_emails`` will populate the actions branch here.
    """
    return [], bool(state.get("emails"))


def _team_cards_view(cards: list) -> list[dict]:
    view = [
        {
            "person": c.person,
            "status": c.status,
            "status_label": c.status.replace("_", " ").title(),
            "in_progress_count": c.in_progress_count,
            "overdue_items": c.overdue_items,
            "inactive_items": c.inactive_items,
            "on_time_rate": c.on_time_rate,
            "summary": c.summary,
        }
        for c in cards
    ]
    view.sort(
        key=lambda x: (
            PERSON_STATUS_ORDER.get(x["status"], 99),
            -x["in_progress_count"],
        )
    )
    return view
