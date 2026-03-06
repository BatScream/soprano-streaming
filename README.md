# langgraph-stream-poc

A proof-of-concept conversational chatbot built with [soprano-sdk](https://pypi.org/project/soprano-sdk/0.2.70/), LangGraph, and FastAPI. The application executes a YAML-defined workflow, streaming intermediate node outputs and LLM tokens to the client in real time via Server-Sent Events (SSE).

## Architecture

The codebase is split into three layers with a clean dependency flow:

```
Controller (main.py)  -->  Service (service.py)  -->  SDK Abstraction (workflow_runner.py)
     thin FastAPI              event mapping              soprano-sdk / LangGraph
```

| Layer | File | Responsibility |
|---|---|---|
| **Controller** | `main.py` | Thin FastAPI app. Defines routes, wires requests to the service, returns `EventSourceResponse`. No SDK imports. |
| **Service** | `service.py` | Receives typed domain events from the runner and maps them to SSE dicts. No SDK imports. |
| **SDK Abstraction** | `workflow_runner.py` | All `soprano-sdk` and `langgraph` imports live here. Handles graph initialisation, streaming, state inspection, and interrupt resolution. Exposes a `run()` generator that yields domain event dataclasses. |
| **Domain Functions** | `functions.py` | Step functions called by the workflow. Uses LangGraph's `get_stream_writer()` for mid-node streaming. |
| **Workflow** | `workflow.yaml` | Declarative YAML workflow definition consumed by soprano-sdk. |
| **Client** | `client.py` | Terminal chat interface that connects to the SSE endpoint. |

## Workflow

The demo workflow (`workflow.yaml`) is a 5-step greeting bot:

```
collect_name -> suggest_colors -> collect_color -> generate_recommendation -> build_greeting
```

1. **collect_name** — AI agent asks for the user's name (`collect_input_with_agent`)
2. **suggest_colors** — Emits a static list of colors via `get_stream_writer()` (`call_function`)
3. **collect_color** — AI agent asks the user to pick a color (`collect_input_with_agent`)
4. **generate_recommendation** — Simulates LLM token streaming for a color recommendation (`call_function`)
5. **build_greeting** — Builds the final greeting message (`call_function`)

## SSE Events

The `/chat` endpoint streams the following event types:

| Event | When | Payload |
|---|---|---|
| `node_complete` | After each LangGraph node finishes | `thread_id`, `node`, `state_update` |
| `custom` | Mid-node, via `get_stream_writer()` | `thread_id` + custom payload (e.g. `type: "color_list"` or `type: "llm_token"`) |
| `interrupt` | Workflow paused for user input | `thread_id`, `prompt`, `options`, `is_selectable`, `field_details` |
| `complete` | Workflow finished | `thread_id`, `message`, `options`, `is_selectable` |
| `error` | Something went wrong | `error` |
| `done` | Stream ended | empty |

### Mid-node streaming

Functions in `functions.py` use LangGraph's `get_stream_writer()` to emit data during execution — before the node returns. This is how the client receives intermediate results in real time:

- **`suggest_colors`** emits a `color_list` event with the static color palette
- **`generate_color_recommendation`** emits `llm_token` events chunk by chunk, simulating token-level LLM streaming

The graph is streamed with `stream_mode=["updates", "custom"]`, which yields both node-completion updates and custom mid-node events.

## Prerequisites

- Python >= 3.12
- An OpenAI API key

## Setup

```bash
# Clone and enter the project
cd langgraph-stream-poc

# Install dependencies with uv
uv sync

# Or with pip
pip install -e .
```

## Running

### Start the server

```bash
export OPENAI_API_KEY="sk-..."
python main.py
```

The server starts on `http://localhost:8000`.

### Start the terminal client

In a separate terminal:

```bash
python client.py
```

### Test with curl

```bash
# Start a conversation
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "hi"}'

# Resume with the thread_id from the interrupt event
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"thread_id": "<thread_id>", "message": "Alice"}'
```

## API

### POST /chat

Request body:

```json
{
  "thread_id": "optional-existing-thread-id",
  "message": "user message text"
}
```

- Omit `thread_id` (or send `null`) to start a new conversation.
- Include `thread_id` from a previous `interrupt` event to resume.

Response: SSE stream (Content-Type: `text/event-stream`).

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OPENAI_API_KEY` | Yes | — | OpenAI API key for the LLM agents |
| `MODEL_NAME` | No | `gpt-4o-mini` | Model to use for agent conversations |

## Project Structure

```
langgraph-stream-poc/
  main.py              # Layer 1: FastAPI controller
  service.py           # Layer 2: SSE event mapping
  workflow_runner.py   # Layer 3: soprano-sdk / LangGraph abstraction
  functions.py         # Workflow step functions
  workflow.yaml        # Workflow definition
  client.py            # Terminal chat client
  pyproject.toml       # Project metadata and dependencies
```
