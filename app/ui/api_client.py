import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from pydantic import ValidationError

from app.schemas.chat import ChatResponse
from app.schemas.task import TaskResponse

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 15


class APIError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _configured_base_url() -> str:
    value = os.getenv("TASK_MANAGER_API_URL", "http://127.0.0.1:8000").strip()
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise APIError(
            "The backend URL is invalid. Set TASK_MANAGER_API_URL to an HTTP or "
            "HTTPS URL without credentials."
        ) from None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise APIError(
            "The backend URL is invalid. Set TASK_MANAGER_API_URL to an HTTP or "
            "HTTPS URL without credentials."
        )
    return value.rstrip("/")


def _http_error_message(status_code: int) -> str:
    if status_code == 422:
        return "The backend rejected the request. Check the message or filter values."
    if status_code == 429:
        return "The chat service is at capacity. Try again shortly."
    if status_code == 502:
        return "The AI provider could not complete the request. Please try again later."
    if status_code == 503:
        return (
            "The backend service is unavailable. Check that it is running and "
            "that chat is configured on the server."
        )
    if status_code in {401, 403}:
        return (
            "The backend denied this request. "
            "Check access with the server administrator."
        )
    if status_code >= 500:
        return f"The backend could not complete the request (HTTP {status_code})."
    return f"The backend rejected the request (HTTP {status_code}). Check your input."


class APIClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or _configured_base_url()).rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | bool] | None = None,
        json_body: dict[str, str] | None = None,
    ) -> object:
        query = urlencode(params or {})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        headers = {"Accept": "application/json"}
        data = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(json_body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)

        try:
            with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
        except HTTPError as exc:
            raise APIError(_http_error_message(exc.code)) from None
        except (URLError, TimeoutError, OSError, HTTPException):
            raise APIError(
                "Unable to connect to the backend. Check that it is running and "
                "TASK_MANAGER_API_URL is correct."
            ) from None

        if not body:
            return None
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise APIError(
                "The backend returned an unexpected response. Please try again later."
            ) from None

    def send_message(self, conversation_id: str, message: str) -> ChatResponse:
        result = self._request(
            "POST",
            "/chat",
            json_body={"conversation_id": conversation_id, "message": message},
        )
        try:
            response = ChatResponse.model_validate(result)
        except ValidationError:
            raise APIError(
                "The backend returned an unexpected chat response. Please try again."
            ) from None
        if response.conversation_id != conversation_id:
            raise APIError(
                "The backend returned a different conversation. Please try again."
            )
        return response

    def list_tasks(
        self,
        filters: dict[str, str | bool] | None = None,
    ) -> list[TaskResponse]:
        params = {
            key: value
            for key, value in (filters or {}).items()
            if value is not False and value != ""
        }
        result = self._request("GET", "/tasks", params=params)
        if not isinstance(result, list):
            raise APIError(
                "The backend returned an unexpected task list. Please try again."
            )
        try:
            return [TaskResponse.model_validate(item) for item in result]
        except ValidationError:
            raise APIError(
                "The backend returned unexpected task data. Please try again."
            ) from None
