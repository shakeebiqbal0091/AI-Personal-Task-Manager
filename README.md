# AI Personal Task Manager

A beginner-friendly foundation for the AI Personal Task Manager. The application includes a FastAPI health endpoint, a Streamlit dashboard shell, SQLite-backed task management through SQLAlchemy, and a controlled Personal Productivity AI Worker using the xAI Grok API. Scheduling features are intentionally not included yet.

## Requirements

- Python 3.10 or newer
- [uv](https://docs.astral.sh/uv/)

## Setup

From the project directory, create and activate a virtual environment:

```powershell
uv venv
.\.venv\Scripts\Activate.ps1
```

Install the project dependencies:

```powershell
uv pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `GROK_API_KEY` to an API key from your xAI account. The application loads `.env` for configuration. The worker uses `GROK_MODEL` (default `grok-4.7`), allows at most 10 agent turns (`AI_MAX_TURNS`, default 5), and bounds in-memory conversation history (`AI_CONTEXT_MAX_MESSAGES`, default 20). Never commit `.env` or share its key.

The Streamlit dashboard connects to the backend over HTTP using `TASK_MANAGER_API_URL` (default `http://127.0.0.1:8000`). Set it in `.env` or the environment if the backend is hosted elsewhere. The dashboard does not connect directly to the database or AI Worker.

The database reads `DATABASE_URL` from the process environment; its default is `sqlite:///./task_manager.db`. Set that variable in your terminal if you want to use a different database URL.

## Run the applications

Start the FastAPI backend:

```powershell
uvicorn app.main:app --reload
```

The health endpoint is available at <http://127.0.0.1:8000/health>. Interactive API documentation is at <http://127.0.0.1:8000/docs>.

## Task API

The backend provides these task endpoints:

- `POST /tasks` — create a task.
- `GET /tasks` — list tasks; filter with `status`, `priority`, `category`, `search`, and `overdue_only`.
- `GET /tasks/{task_id}` — retrieve a task.
- `PATCH /tasks/{task_id}` — update task fields or status.
- `DELETE /tasks/{task_id}` — review the task and request deletion confirmation.

To delete a task, first call `DELETE /tasks/{task_id}`. The API responds with the task details and its `updated_at` value. After reviewing that exact task, repeat the request with `confirm=true` and the returned `expected_updated_at` value. If the task changed since it was presented, the deletion is rejected and must be reviewed again.

## AI Worker foundation

The worker is available in `app/worker/` for application code to call asynchronously. It connects to the xAI API at `https://api.x.ai/v1` using the Agents SDK's OpenAI-compatible Chat Completions model. Conversation context is held in memory and is not written to the task database.

The worker can create, list, retrieve, update, and complete tasks when called with a database session. It cannot delete tasks. Provider requests require a valid xAI API key and may incur provider charges. Automated tests use mocked SDK model responses and do not call xAI.

The FastAPI `POST /chat` endpoint accepts a `conversation_id` and a non-empty `message`, and returns the worker's response with that conversation ID. The first request for an ID initializes a conversation; recent messages are held in bounded process memory and are isolated by ID. Requests for the same conversation are serialized. New conversations are rejected with HTTP 429 when the process-local limit is reached instead of silently evicting existing context. Conversation history is not persisted and is lost when the process restarts; deployments with multiple workers do not share conversation context.

The chat endpoint obtains a request-scoped SQLAlchemy session and passes it to the worker's authorized task tools. The shared worker is created lazily on the first chat request and is closed during application shutdown. Missing xAI configuration makes chat unavailable with HTTP 503 while leaving the task API usable.

The `openai-agents` package currently declares MCP as a required dependency. This application does not configure MCP servers, tools, or integrations.

Create `ProductivityWorker` and `ConversationContext` in async application code, call `await worker.respond(message, context, session=session)`, and close the worker with `await worker.close()` when finished. Pass a request-scoped SQLAlchemy session; create one conversation context per conversation. Context is temporary and bounded by `AI_CONTEXT_MAX_MESSAGES`.

In a second terminal, with the virtual environment activated, start the Streamlit dashboard:

```powershell
streamlit run app/ui/dashboard.py
```

Use the **Chat** and **Tasks** navigation to switch between the assistant and the task list. Chat requests use the current browser session's conversation ID; **New conversation** creates a fresh ID and clears the locally displayed messages. The dashboard keeps only messages available in that browser session and does not provide persisted conversation history. The backend's conversation context is process-local, so it may be lost after a restart or unavailable across multiple backend workers. A successful chat response marks the task list for refresh so changes made by AI task tools appear the next time Tasks is opened.

## Run the tests

```powershell
pytest
```
