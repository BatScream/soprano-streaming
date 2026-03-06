"""Layer 2 — Chat service. Maps domain events from the workflow runner
into SSE-ready dicts for the controller."""

import json
import uuid
from typing import Generator, Optional

from workflow_runner import (
    WorkflowRunner,
    NodeCompleteEvent,
    CustomEvent,
    InterruptEvent,
    CompleteEvent,
    ErrorEvent,
)


class ChatService:
    def __init__(self, runner: WorkflowRunner):
        self._runner = runner

    @classmethod
    def create(cls) -> "ChatService":
        """Factory that builds the full stack from environment variables."""
        return cls(WorkflowRunner.from_env())

    def handle_message(
        self, thread_id: Optional[str], message: Optional[str]
    ) -> Generator[dict, None, None]:
        """Return a generator of ``{"event": ..., "data": ...}`` dicts
        suitable for ``EventSourceResponse``."""
        thread_id = thread_id or str(uuid.uuid4())

        for event in self._runner.run(thread_id, message):
            yield self._to_sse(thread_id, event)

        yield {"event": "done", "data": ""}

    # ------------------------------------------------------------------
    # Internal — pure mapping, no SDK knowledge
    # ------------------------------------------------------------------

    @staticmethod
    def _to_sse(thread_id: str, event) -> dict:
        match event:
            case NodeCompleteEvent():
                return {
                    "event": "node_complete",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        "node": event.node,
                        "state_update": event.state_update,
                    }),
                }
            case CustomEvent():
                return {
                    "event": "custom",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        **event.payload,
                    }),
                }
            case InterruptEvent():
                return {
                    "event": "interrupt",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        "type": "user_input",
                        "prompt": event.prompt,
                        "options": event.options,
                        "is_selectable": event.is_selectable,
                        "field_details": event.field_details,
                    }),
                }
            case CompleteEvent():
                return {
                    "event": "complete",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        "message": event.message,
                        "options": event.options,
                        "is_selectable": event.is_selectable,
                    }),
                }
            case ErrorEvent():
                return {
                    "event": "error",
                    "data": json.dumps({"error": event.error}),
                }
