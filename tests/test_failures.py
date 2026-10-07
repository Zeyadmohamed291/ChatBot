import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import BackendUnavailable
from app.gateway import LaravelGateway, MockGateway
from app.main import create_app
from app.models import Search


def test_request_deadline_falls_back_without_backend_lookup(settings, headers):
    settings.request_timeout_seconds = 0.02

    class SlowPlanner:
        async def plan(self, *args):
            await asyncio.sleep(1)
            raise AssertionError("The deadline should have cancelled this coroutine")

        async def close(self):
            pass

    gateway = MockGateway()
    gateway.order = AsyncMock(wraps=gateway.order)
    with TestClient(create_app(settings, gateway=gateway, planner=SlowPlanner())) as client:
        body = client.post(
            "/v1/chat", headers=headers, json={"message": "Where is my order?"}
        ).json()
        assert body["degraded"]
        gateway.order.assert_not_called()


async def test_laravel_network_timeout_and_oversized_response(settings):
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")

    for handler in [timeout, lambda request: httpx.Response(200, content=b"x" * (1024 * 1024 + 1))]:
        client = httpx.AsyncClient(
            base_url="https://backend.example", transport=httpx.MockTransport(handler)
        )
        gateway = LaravelGateway(settings, client)
        with pytest.raises(BackendUnavailable):
            await gateway.products(Search())
        await gateway.close()


def test_production_configuration_can_be_completed():
    configured = Settings(
        _env_file=None,
        environment="production",
        planner_mode="openai",
        backend_mode="laravel",
        ai_service_token="a" * 40,
        openai_api_key="test",
        openai_model="test-model",
        backend_api_token="test",
        backend_base_url="https://backend.example",
        allowed_catalog_hosts=["shop.example"],
        redis_url="redis://localhost:6379/0",
    )
    assert configured.environment == "production"
