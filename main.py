"""Layer 1 — Thin FastAPI controller. Delegates all logic to ChatService."""

from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from service import ChatService

app = FastAPI(title="Greeting Bot")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

chat_service: ChatService = None  # type: ignore[assignment]


@app.on_event("startup")
def startup():
    global chat_service
    chat_service = ChatService.create()


class ChatRequest(BaseModel):
    thread_id: Optional[str] = None
    message: Optional[str] = None


@app.post("/chat")
def chat(req: ChatRequest):
    return EventSourceResponse(
        chat_service.handle_message(req.thread_id, req.message)
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
