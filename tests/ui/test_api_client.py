import json
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from app.ui.api_client import APIClient, APIError


class MockResponse:
    def __init__(self, value: object) -> None:
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "MockResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def task_response() -> dict[str, object]:
    return {
        "id": 7,
        "title": "Prepare report",
        "description": "Draft the weekly summary",
        "priority": "High",
        "status": "Pending",
        "category": "Work",
        "due_date": "2026-12-01T10:00:00",
        "estimated_duration": 30,
        "created_at": "2026-11-01T09:00:00",
        "updated_at": "2026-11-01T09:00:00",
        "completed_at": None,
    }


def test_chat_submission_uses_request_schema_and_preserves_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def mock_urlopen(request, timeout):
        requests.append((request, timeout))
        return MockResponse(
            {"conversation_id": "conversation-123", "response": "Task added."}
        )

    monkeypatch.setattr("app.ui.api_client.urlopen", mock_urlopen)
    client = APIClient("http://localhost:8000")

    response = client.send_message("conversation-123", "Add a task")

    assert response.conversation_id == "conversation-123"
    assert response.response == "Task added."
    request, timeout = requests[0]
    assert request.full_url == "http://localhost:8000/chat"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {
        "conversation_id": "conversation-123",
        "message": "Add a task",
    }
    assert timeout > 0


def test_task_retrieval_sends_selected_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = []

    def mock_urlopen(request, timeout):
        requests.append(request)
        return MockResponse([task_response()])

    monkeypatch.setattr("app.ui.api_client.urlopen", mock_urlopen)
    client = APIClient("http://localhost:8000")

    tasks = client.list_tasks(
        {
            "status": "Pending",
            "priority": "High",
            "search": "report",
            "overdue_only": True,
        }
    )

    assert len(tasks) == 1
    assert tasks[0].title == "Prepare report"
    parsed_url = urlsplit(requests[0].full_url)
    assert parsed_url.path == "/tasks"
    assert parse_qs(parsed_url.query) == {
        "status": ["Pending"],
        "priority": ["High"],
        "search": ["report"],
        "overdue_only": ["True"],
    }


def test_backend_url_comes_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASK_MANAGER_API_URL", "https://tasks.example.test/api")

    assert APIClient().base_url == "https://tasks.example.test/api"


def test_invalid_backend_url_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TASK_MANAGER_API_URL", "https://user:secret@example.test")

    with pytest.raises(APIError, match="backend URL is invalid") as error:
        APIClient()

    assert "secret" not in error.value.message


def test_connection_failure_does_not_expose_exception_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_connect(*_, **__):
        raise URLError("private credential details")

    monkeypatch.setattr("app.ui.api_client.urlopen", fail_to_connect)

    with pytest.raises(APIError) as error:
        APIClient("http://localhost:8000").send_message("conversation", "Hello")

    assert "Unable to connect" in error.value.message
    assert "private credential" not in error.value.message


@pytest.mark.parametrize(
    ("status_code", "message"),
    [
        (422, "rejected the request"),
        (502, "AI provider could not complete"),
        (503, "backend service is unavailable"),
    ],
)
def test_validation_and_provider_errors_are_clear_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    message: str,
) -> None:
    def fail_request(*_, **__):
        raise HTTPError(
            "http://localhost:8000/chat",
            status_code,
            "failure",
            {},
            BytesIO(b'{"detail":"private implementation detail"}'),
        )

    monkeypatch.setattr("app.ui.api_client.urlopen", fail_request)

    with pytest.raises(APIError) as error:
        APIClient("http://localhost:8000").send_message("conversation", "Hello")

    assert message in error.value.message
    assert "private implementation detail" not in error.value.message
