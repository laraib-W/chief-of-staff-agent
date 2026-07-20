"""StateGraph wiring only. Node implementations live in `app.nodes`.

fetch_emails is the first real sensor node. fetch_calendar and fetch_plane
will join it as parallel fan-out from START once their tickets land; each
Phase 1+ node ticket extends this wiring as its node lands.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config.loader import Config
from app.nodes.fetch_emails import fetch_emails_node
from app.schemas.digest import AgentState


def build_graph(
    config: Config, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("fetch_emails", fetch_emails_node(config))
    graph.add_edge(START, "fetch_emails")
    graph.add_edge("fetch_emails", END)
    return graph.compile(checkpointer=checkpointer)
