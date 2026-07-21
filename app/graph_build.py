"""
Builds the compiled LangGraph state machine.

Routing:
  PLAN --(is_final)--> END
  PLAN --(step cap exceeded)--> STEP_CAP --> END
  PLAN --(else)--> PERMISSION
  PERMISSION --(allow)--> EXECUTE
  PERMISSION --(deny)--> DENIED --> PLAN
  PERMISSION --(requires_approval)--> APPROVAL_WAIT   [graph interrupts HERE]
  APPROVAL_WAIT --(approved)--> EXECUTE
  APPROVAL_WAIT --(rejected)--> PLAN
  EXECUTE --> PLAN

The interrupt happens BEFORE approval_wait_node runs, via
compile(interrupt_before=["approval_wait"]). That means: the checkpointer
saves state with permission_decision="requires_approval" and the graph
literally stops executing -- it is not "running and blocked in a loop", the
Python call stack has returned control to the caller. Resuming later
(potentially from a fresh process, since state is in SQLite, not memory)
re-invokes the graph, which continues from approval_wait_node.
"""
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.graph_nodes import (
    AgentContext,
    approval_wait_node,
    denied_node,
    execute_node,
    plan_node,
    permission_node,
    step_cap_node,
)
from app.graph_state import AgentState


def _route_after_plan(state: AgentState) -> str:
    if state.get("is_final"):
        return END
    if state["step_number"] > state.get("max_steps", 8):
        return "step_cap"
    return "permission"


def _route_after_permission(state: AgentState) -> str:
    decision = state.get("permission_decision")
    if decision == "allow":
        return "execute"
    if decision == "deny":
        return "denied"
    if decision == "requires_approval":
        return "approval_wait"
    raise ValueError(f"Unexpected permission_decision: {decision!r}")


def _route_after_approval(state: AgentState) -> str:
    if state.get("approval_decision") == "approved":
        return "execute"
    return "plan"  # rejected, or no decision -- go back to planning, never silently execute


def build_graph(sqlite_path: str = "agent_state.db"):
    """
    Returns (compiled_graph, checkpointer_cm). The context-manager object is
    returned too so callers/tests can close it cleanly; SqliteSaver.from_conn_string
    is a context-manager-friendly factory in this LangGraph version.
    """
    graph = StateGraph(AgentState, context_schema=AgentContext)

    graph.add_node("plan", plan_node)
    graph.add_node("permission", permission_node)
    graph.add_node("execute", execute_node)
    graph.add_node("denied", denied_node)
    graph.add_node("approval_wait", approval_wait_node)
    graph.add_node("step_cap", step_cap_node)

    graph.set_entry_point("plan")

    graph.add_conditional_edges("plan", _route_after_plan,
                                 {"permission": "permission", "step_cap": "step_cap", END: END})
    graph.add_conditional_edges("permission", _route_after_permission,
                                 {"execute": "execute", "denied": "denied",
                                  "approval_wait": "approval_wait"})
    graph.add_conditional_edges("approval_wait", _route_after_approval,
                                 {"execute": "execute", "plan": "plan"})

    graph.add_edge("execute", "plan")
    graph.add_edge("denied", "plan")
    graph.add_edge("step_cap", END)

    checkpointer_cm = SqliteSaver.from_conn_string(sqlite_path)
    checkpointer = checkpointer_cm.__enter__()

    compiled = graph.compile(checkpointer=checkpointer, interrupt_before=["approval_wait"])
    return compiled, checkpointer_cm
