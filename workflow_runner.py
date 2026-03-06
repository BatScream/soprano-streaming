"""Layer 3 — SDK abstraction. Owns the soprano-sdk graph and exposes
a high-level event stream of domain objects.

No other layer should import from soprano_sdk or langgraph."""

import os
from dataclasses import dataclass, field
from typing import Any, Generator, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from soprano_sdk import load_workflow
from soprano_sdk.core.constants import WorkflowKeys
from soprano_sdk.core.engine import WorkflowEngine


# ------------------------------------------------------------------
# Domain events — the public contract consumed by layer 2
# ------------------------------------------------------------------


@dataclass
class NodeCompleteEvent:
    node: str
    state_update: dict


@dataclass
class CustomEvent:
    payload: dict


@dataclass
class InterruptEvent:
    prompt: str
    options: list = field(default_factory=list)
    is_selectable: bool = False
    field_details: list = field(default_factory=list)


@dataclass
class CompleteEvent:
    message: str
    options: list = field(default_factory=list)
    is_selectable: bool = False


@dataclass
class ErrorEvent:
    error: str


# Union type for convenience
WorkflowEvent = NodeCompleteEvent | CustomEvent | InterruptEvent | CompleteEvent | ErrorEvent


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------


class WorkflowRunner:

    def __init__(self, graph: CompiledStateGraph, engine: WorkflowEngine):
        self._graph = graph
        self._engine = engine

    @classmethod
    def from_env(cls, yaml_path: str = "workflow.yaml") -> "WorkflowRunner":
        """Build a runner from environment variables.

        Required env vars:
            OPENAI_API_KEY
        Optional env vars:
            MODEL_NAME  (default: gpt-4o-mini)
        """
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")

        checkpointer = InMemorySaver()
        graph, engine = load_workflow(
            yaml_path,
            checkpointer=checkpointer,
            config={
                "model_config": {
                    "model_name": os.environ.get("MODEL_NAME", "gpt-4o-mini"),
                    "api_key": api_key,
                },
            },
        )
        return cls(graph, engine)

    def run(
        self, thread_id: str, message: Optional[str]
    ) -> Generator[WorkflowEvent, None, None]:
        """Yield high-level domain events for a single conversation turn.

        The caller never needs to understand LangGraph internals.
        """
        try:
            yield from self._stream_graph(thread_id, message)
            yield self._resolve_final_state(thread_id)
        except Exception as exc:
            yield ErrorEvent(error=str(exc))

    # ------------------------------------------------------------------
    # Internal — all SDK detail is below this line
    # ------------------------------------------------------------------

    def _stream_graph(
        self, thread_id: str, message: Optional[str]
    ) -> Generator[NodeCompleteEvent | CustomEvent, None, None]:
        config = self._thread_config(thread_id)
        state = self._graph.get_state(config)

        if state.next:
            input_val = Command(
                resume=message or "",
                update={WorkflowKeys.USER_MESSAGE: message or ""},
            )
        else:
            input_val = {WorkflowKeys.USER_MESSAGE: message or ""}

        for mode, chunk in self._graph.stream(
            input_val, config=config, stream_mode=["updates", "custom"]
        ):
            if mode == "updates":
                for node_name, update in chunk.items():
                    yield NodeCompleteEvent(
                        node=node_name, state_update=_serialize(update)
                    )
            elif mode == "custom":
                yield CustomEvent(payload=_serialize(chunk))

    def _resolve_final_state(self, thread_id: str) -> InterruptEvent | CompleteEvent:
        config = self._thread_config(thread_id)
        final_state = self._graph.get_state(config)

        if final_state.next:
            return self._build_interrupt(final_state)
        return self._build_complete(final_state, thread_id)

    def _build_interrupt(self, final_state) -> InterruptEvent:
        interrupt_val = (
            final_state.tasks[0].interrupts[0].value
            if final_state.tasks
            else None
        )

        if isinstance(interrupt_val, dict) and "text" in interrupt_val:
            prompt = interrupt_val["text"]
            options = interrupt_val.get("options", [])
            is_selectable = interrupt_val.get("is_selectable", False)
        else:
            prompt = str(interrupt_val) if interrupt_val else ""
            options = []
            is_selectable = False

        field_details = self._engine.build_field_details(
            final_state.values, node_id=final_state.next[0]
        )

        return InterruptEvent(
            prompt=prompt,
            options=options,
            is_selectable=is_selectable,
            field_details=field_details,
        )

    def _build_complete(self, final_state, thread_id: str) -> CompleteEvent:
        outcome = self._engine.get_outcome_message(
            final_state.values,
            thread_id=thread_id,
            workflow_name=self._engine.workflow_name,
        )
        return CompleteEvent(
            message=outcome.message,
            options=outcome.options or [],
            is_selectable=outcome.is_selectable,
        )

    @staticmethod
    def _thread_config(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}


def _serialize(obj: Any) -> Any:
    """Best-effort JSON-safe serialisation of state updates."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)
