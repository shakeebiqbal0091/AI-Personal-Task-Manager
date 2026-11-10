import asyncio
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlencode

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database.database import Base, get_db
from app.main import app


class ASGIClient:
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        return asyncio.run(
            self.async_request(
                method,
                path,
                params=params,
                json_body=json_body,
            )
        )

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, Any] | None]:
        query_string = urlencode(params or {})
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_string.encode("ascii"),
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
        messages: list[dict[str, Any]] = []
        request_sent = False

        async def receive() -> dict[str, Any]:
            nonlocal request_sent
            if request_sent:
                return {"type": "http.disconnect"}
            request_sent = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        await app(scope, receive, send)
        response_start = next(
            message
            for message in messages
            if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"")
            for message in messages
            if message["type"] == "http.response.body"
        )
        response_data = json.loads(response_body) if response_body else None
        return response_start["status"], response_data


@pytest.fixture
def test_engine() -> Iterator[Engine]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    with Session(test_engine) as session:
        yield session


@pytest.fixture
def client(test_engine: Engine) -> Iterator[ASGIClient]:
    previous_overrides = app.dependency_overrides.copy()

    def get_test_db() -> Iterator[Session]:
        with Session(test_engine) as session:
            yield session

    app.dependency_overrides[get_db] = get_test_db
    try:
        yield ASGIClient()
    finally:
        app.dependency_overrides = previous_overrides
