"""StateGraph wiring only. Node implementations live in `app.nodes`.

fetch_emails, fetch_calendar, and fetch_plane run as parallel fan-out from
START. fetch_plane feeds assess_team → correlate, which folds team_health
(plus email_actions and day_analysis when their nodes land) into the
top-priority list. All three sensor branches fan in to render_and_deliver
so the digest ships even when correlation produced nothing. Every sensor
writes to ``errors`` in parallel, so ``AgentState.errors`` uses a
dict-union reducer (see app/schemas/digest.py).
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config.loader import Config
from app.nodes.assess_team import assess_team_node
from app.nodes.correlate import correlate_node
from app.nodes.fetch_calendar import fetch_calendar_node
from app.nodes.fetch_emails import fetch_emails_node
from app.nodes.fetch_plane import fetch_plane_node
from app.nodes.render_and_deliver import render_and_deliver_node
from app.schemas.digest import AgentState


def build_graph(
    config: Config, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("fetch_emails", fetch_emails_node(config))
    graph.add_node("fetch_calendar", fetch_calendar_node(config))
    graph.add_node("fetch_plane", fetch_plane_node(config))
    graph.add_node("assess_team", assess_team_node(config))
    graph.add_node("correlate", correlate_node(config))
    graph.add_node("render_and_deliver", render_and_deliver_node(config))

    graph.add_edge(START, "fetch_emails")
    graph.add_edge(START, "fetch_calendar")
    graph.add_edge(START, "fetch_plane")

    graph.add_edge("fetch_plane", "assess_team")
    graph.add_edge("assess_team", "correlate")

    # Fan in to the tail: render_and_deliver waits for every upstream branch
    # so the digest sees the full state, not just whichever sensor finished
    # first.
    graph.add_edge("fetch_emails", "render_and_deliver")
    graph.add_edge("fetch_calendar", "render_and_deliver")
    graph.add_edge("correlate", "render_and_deliver")

    graph.add_edge("render_and_deliver", END)

    return graph.compile(checkpointer=checkpointer)
