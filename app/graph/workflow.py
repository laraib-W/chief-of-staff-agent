"""StateGraph wiring only. Node implementations live in `app.nodes`.

fetch_emails and fetch_plane run as parallel fan-out from START. fetch_calendar
will join them once its ticket lands.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.config.loader import Config
from app.nodes.fetch_emails import fetch_emails_node
from app.nodes.fetch_plane import fetch_plane
from app.schemas.digest import AgentState


def build_graph(
    config: Config, checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("fetch_emails", fetch_emails_node(config))
    graph.add_node("fetch_plane", fetch_plane)
    graph.add_edge(START, "fetch_emails")
    graph.add_edge(START, "fetch_plane")
    graph.add_edge("fetch_emails", END)
    graph.add_edge("fetch_plane", END)
    return graph.compile(checkpointer=checkpointer)
