"""fetch_calendar sensor node (specs.md §3.1.2)."""

from collections.abc import Callable

import structlog

from app.config.loader import Config
from app.providers import calendar
from app.schemas.digest import AgentState

log = structlog.get_logger(__name__)


def fetch_calendar_node(config: Config) -> Callable[[AgentState], AgentState]:
    """Build the fetch_calendar sensor node bound to a loaded Config.

    Fetches today's events + a 7-day look-ahead via the Calendar provider,
    writing ``calendar_events`` and ``errors["calendar"]`` per the sensor
    contract in specs.md §3.1.2.
    """

    def _node(state: AgentState) -> AgentState:
        events, error = calendar.CalendarClient(config).fetch_events()
        if error:
            log.warning("fetch_calendar_node.error", error=error)
        else:
            log.info("fetch_calendar_node.ok", count=len(events))
        errors = dict(state.get("errors") or {})
        errors["calendar"] = error
        return {"calendar_events": events, "errors": errors}

    return _node
