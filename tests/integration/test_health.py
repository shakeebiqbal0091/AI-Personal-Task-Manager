from fastapi.routing import APIRoute

from app.main import app


def test_health_endpoint_returns_ok() -> None:
    health_route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/health"
    )

    assert health_route.endpoint() == {"status": "ok"}
