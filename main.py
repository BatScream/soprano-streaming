import json
import os
import uuid
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from soprano_sdk import load_workflow
from soprano_sdk.core.constants import WorkflowKeys

app = FastAPI(title="Greeting Bot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

checkpointer = InMemorySaver()
graph = None
engine = None


@app.on_event("startup")
def startup():
    global graph, engine
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is not set")
    graph, engine = load_workflow(
        "workflow.yaml",
        checkpointer=checkpointer,
        config={
            "model_config": {
                "model_name": os.environ.get("MODEL_NAME", "gpt-4o-mini"),
                "api_key": api_key,
            },
        },
    )


class ChatRequest(BaseModel):
    thread_id: Optional[str] = None
    message: Optional[str] = None


@app.post("/chat")
def chat(req: ChatRequest):
    thread_id = req.thread_id or str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    state = graph.get_state(config)

    def event_stream():
        try:
            if state.next:
                input_val = Command(
                    resume=req.message or "",
                    update={WorkflowKeys.USER_MESSAGE: req.message or ""},
                )
            else:
                input_val = {WorkflowKeys.USER_MESSAGE: req.message or ""}

            # Stream both node-level updates and custom mid-node events
            for mode, chunk in graph.stream(
                input_val,
                config=config,
                stream_mode=["updates", "custom"],
            ):
                if mode == "updates":
                    # Emitted once per node after it finishes
                    for node_name, update in chunk.items():
                        yield {
                            "event": "node_complete",
                            "data": json.dumps({
                                "thread_id": thread_id,
                                "node": node_name,
                                "state_update": _serialize(update),
                            }),
                        }
                elif mode == "custom":
                    # Emitted mid-node via get_stream_writer()
                    yield {
                        "event": "custom",
                        "data": json.dumps({
                            "thread_id": thread_id,
                            **_serialize(chunk),
                        }),
                    }

            # After streaming, inspect final state
            final_state = graph.get_state(config)

            if final_state.next:
                # Workflow interrupted — needs user input
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

                yield {
                    "event": "interrupt",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        "type": "user_input",
                        "prompt": prompt,
                        "options": options,
                        "is_selectable": is_selectable,
                        "field_details": engine.build_field_details(
                            final_state.values, node_id=final_state.next[0]
                        ),
                    }),
                }
            else:
                # Workflow completed
                outcome = engine.get_outcome_message(
                    final_state.values,
                    thread_id=thread_id,
                    workflow_name=engine.workflow_name,
                )
                yield {
                    "event": "complete",
                    "data": json.dumps({
                        "thread_id": thread_id,
                        "message": outcome.message,
                        "options": outcome.options or [],
                        "is_selectable": outcome.is_selectable,
                    }),
                }

            yield {"event": "done", "data": ""}

        except Exception as exc:
            yield {
                "event": "error",
                "data": json.dumps({"error": str(exc)}),
            }

    return EventSourceResponse(event_stream())


def _serialize(obj):
    """Best-effort JSON-safe serialisation of state updates."""
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(v) for v in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
