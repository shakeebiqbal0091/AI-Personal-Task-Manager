import pytest
from sqlalchemy.exc import OperationalError

from app.database.database import get_db
from app.main import app


def create_task(client, **values):
    task = {"title": "Prepare report", **values}
    status_code, response = client.request("POST", "/tasks", json_body=task)
    assert status_code == 201
    return response


def test_create_and_retrieve_task(client) -> None:
    created = create_task(
        client,
        description="Draft the weekly summary",
        category="Work",
        estimated_duration=45,
    )

    status_code, retrieved = client.request("GET", f"/tasks/{created['id']}")

    assert status_code == 200
    assert retrieved["title"] == "Prepare report"
    assert retrieved["priority"] == "Medium"
    assert retrieved["status"] == "Pending"
    assert retrieved["category"] == "Work"
    assert retrieved["estimated_duration"] == 45
    assert retrieved["created_at"] is not None
    assert retrieved["updated_at"] is not None


def test_update_completion_and_invalid_transition(client) -> None:
    task = create_task(client)
    status_code, updated = client.request(
        "PATCH",
        f"/tasks/{task['id']}",
        json_body={"title": "Finish report", "status": "In Progress"},
    )

    assert status_code == 200
    assert updated["title"] == "Finish report"
    assert updated["status"] == "In Progress"

    status_code, completed = client.request(
        "PATCH",
        f"/tasks/{task['id']}",
        json_body={"status": "Completed"},
    )
    assert status_code == 200
    assert completed["completed_at"] is not None

    status_code, failure = client.request(
        "PATCH",
        f"/tasks/{task['id']}",
        json_body={"status": "Cancelled"},
    )

    assert status_code == 409
    assert "Cannot change task status" in failure["detail"]


def test_reopening_completed_task_clears_completion_time(client) -> None:
    task = create_task(client, status="Completed")
    assert task["completed_at"] is not None

    status_code, reopened = client.request(
        "PATCH",
        f"/tasks/{task['id']}",
        json_body={"status": "Pending"},
    )

    assert status_code == 200
    assert reopened["completed_at"] is None


def test_delete_requires_confirmation_for_the_presented_task(client) -> None:
    task = create_task(client)
    status_code, prompt = client.request("DELETE", f"/tasks/{task['id']}")

    assert status_code == 409
    assert prompt["detail"]["task"]["id"] == task["id"]
    assert prompt["detail"]["task"]["title"] == task["title"]

    status_code, response = client.request(
        "DELETE",
        f"/tasks/{task['id']}",
        params={
            "confirm": "true",
            "expected_updated_at": prompt["detail"]["task"]["updated_at"],
        },
    )

    assert status_code == 204
    assert response is None
    assert client.request("GET", f"/tasks/{task['id']}")[0] == 404


def test_delete_rejects_stale_confirmation(client) -> None:
    task = create_task(client)
    _, prompt = client.request("DELETE", f"/tasks/{task['id']}")
    client.request(
        "PATCH",
        f"/tasks/{task['id']}",
        json_body={"title": "Changed after review"},
    )

    status_code, failure = client.request(
        "DELETE",
        f"/tasks/{task['id']}",
        params={
            "confirm": "true",
            "expected_updated_at": prompt["detail"]["task"]["updated_at"],
        },
    )

    assert status_code == 409
    assert "changed after it was presented" in failure["detail"]


def test_list_filters_and_overdue_detection(client) -> None:
    create_task(
        client,
        title="Overdue work report",
        priority="High",
        category="Work",
        due_date="2000-01-01T10:00:00",
    )
    create_task(
        client,
        title="Overdue learning notes",
        status="In Progress",
        category="Learning",
        due_date="2000-01-02T10:00:00",
    )
    create_task(
        client,
        title="Completed old report",
        status="Completed",
        due_date="2000-01-03T10:00:00",
    )
    create_task(
        client,
        title="Future work report",
        priority="High",
        category="Work",
        due_date="2999-01-01T10:00:00",
    )

    status_code, overdue = client.request(
        "GET", "/tasks", params={"overdue_only": "true"}
    )
    assert status_code == 200
    assert {task["title"] for task in overdue} == {
        "Overdue work report",
        "Overdue learning notes",
    }

    status_code, filtered = client.request(
        "GET",
        "/tasks",
        params={
            "status": "Pending",
            "priority": "High",
            "category": "Work",
            "search": "report",
        },
    )
    assert status_code == 200
    assert [task["title"] for task in filtered] == [
        "Overdue work report",
        "Future work report",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "   "},
        {"title": "Valid title", "priority": "Urgent"},
        {"title": "Valid title", "status": "Blocked"},
        {"title": "Valid title", "estimated_duration": 0},
        {"title": "Valid title", "unexpected": True},
    ],
)
def test_create_rejects_invalid_input(client, payload) -> None:
    status_code, response = client.request("POST", "/tasks", json_body=payload)

    assert status_code == 422
    assert response["detail"]


def test_patch_rejects_empty_or_null_required_fields(client) -> None:
    task = create_task(client)

    assert client.request("PATCH", f"/tasks/{task['id']}", json_body={})[0] == 422
    assert (
        client.request(
            "PATCH",
            f"/tasks/{task['id']}",
            json_body={"title": None},
        )[0]
        == 422
    )


def test_invalid_and_missing_task_ids(client) -> None:
    assert client.request("GET", "/tasks/0")[0] == 422
    assert client.request("GET", "/tasks/not-an-id")[0] == 422
    status_code, response = client.request("GET", "/tasks/999")
    assert status_code == 404
    assert response["detail"] == "Task not found."


@pytest.mark.parametrize(
    "params",
    [
        {"status": "Blocked"},
        {"priority": "Urgent"},
        {"category": "   "},
        {"search": "   "},
    ],
)
def test_list_rejects_invalid_filters(client, params) -> None:
    status_code, response = client.request("GET", "/tasks", params=params)

    assert status_code == 422
    assert response["detail"]


def test_database_failure_returns_service_unavailable(client) -> None:
    previous_override = app.dependency_overrides[get_db]

    def database_failure():
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    app.dependency_overrides[get_db] = database_failure
    try:
        status_code, response = client.request("GET", "/tasks")
    finally:
        app.dependency_overrides[get_db] = previous_override

    assert status_code == 503
    assert "database operation failed" in response["detail"].lower()
