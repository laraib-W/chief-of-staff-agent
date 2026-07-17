"""fetch_emails sensor node (specs.md §3.1.1)."""

from collections.abc import Callable

from app.config.loader import Config
from app.providers import gmail
from app.schemas.digest import AgentState


def fetch_emails_node(config: Config) -> Callable[[AgentState], AgentState]:
    """Build the fetch_emails sensor node bound to a loaded Config.

    Fetches and sanitizes email via the Gmail provider, writing ``emails``
    and ``errors["gmail"]`` per the sensor contract in specs.md §3.1.1.
    """

    def _node(state: AgentState) -> AgentState:
        emails, error = gmail.fetch_emails(config)
        errors = dict(state.get("errors") or {})
        errors["gmail"] = error
        return {"emails": emails, "errors": errors}

    return _node
