"""StateGraph wiring only. Node implementations live in `app.nodes`.

Phase 0 ships an empty graph with a single passthrough node so `python -m app.run`
executes end-to-end. Real fan-out and fan-in land as each Phase 1+ node ticket
replaces the placeholder.
"""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.schemas.digest import AgentState


def _placeholder(state: AgentState) -> AgentState:
    return {"errors": state.get("errors", {})}


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    graph = StateGraph(AgentState)
    graph.add_node("placeholder", _placeholder)
    graph.add_edge(START, "placeholder")
    graph.add_edge("placeholder", END)
    return graph.compile(checkpointer=checkpointer)
