import json
import time

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from app.config import Settings
from app.errors import BackendUnavailable, OrderUnavailable, PlannerUnavailable
from app.gateway import LaravelGateway, MockGateway, safe_url
from app.models import Decision, Search, Session
from app.planner import OpenAIPlanner
from app.text import order_number_from_message


async def test_real_openai_sdk_request_and_strict_tool_response(settings):
    seen = []
    result = Decision(
        action="products",
        language="ar",
        query="عطر",
        min_price=None,
        max_price=200,
        order_number=None,
        question="none",
        conversation_kind="capabilities",
    )

    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "test-model",
                "output": [
                    {
                        "id": "fc_test",
                        "type": "function_call",
                        "call_id": "call_test",
                        "name": "route_customer_message",
                        "arguments": result.model_dump_json(),
                        "status": "completed",
                    }
                ],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "total_tokens": 140,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    client = AsyncOpenAI(
        api_key="fake-test-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        max_retries=0,
    )
    settings.openai_model = "test-model"
    planner = OpenAIPlanner(settings, client)
    session = Session(id="test", owner="hashed", expires_at=time.time() + 100)
    planned = await planner.plan("عطر تحت 200", session, "ar")
    assert planned.decision.max_price == 200
    assert planned.input_tokens == 100 and planned.output_tokens == 40
    assert seen[0]["store"] is False
    assert seen[0]["parallel_tool_calls"] is False
    schema = seen[0]["tools"][0]["parameters"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "hashed" not in json.dumps(seen[0])
    await planner.close()


@pytest.mark.parametrize(
    "status,output",
    [
        ("incomplete", []),
        ("completed", []),
        (
            "completed",
            [
                {
                    "type": "function_call",
                    "id": "fc_x",
                    "call_id": "c",
                    "name": "execute_payment",
                    "arguments": "{}",
                }
            ],
        ),
    ],
)
async def test_invalid_model_output_is_rejected(settings, status, output):
    def handler(request):
        return httpx.Response(
            200,
            json={
                "id": "resp_x",
                "created_at": 1,
                "object": "response",
                "model": "test",
                "status": status,
                "output": output,
            },
        )

    client = AsyncOpenAI(
        api_key="fake", http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    planner = OpenAIPlanner(settings, client)
    with pytest.raises(PlannerUnavailable):
        await planner.plan("Hi", Session(id="s", owner="o", expires_at=time.time() + 10), "en")
    await planner.close()


async def test_laravel_search_contract(settings):
    seen = []
    mock = MockGateway()

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": [mock.product_data[0].model_dump(mode="json")]})

    client = httpx.AsyncClient(
        base_url="https://backend.example", transport=httpx.MockTransport(handle)
    )
    gateway = LaravelGateway(settings, client)
    result = await gateway.products(Search(query="perfume", max_price=200))
    assert result[0].id == "demo-product-1"
    assert seen[0].method == "GET"
    assert "store_id" not in seen[0].url.params
    assert seen[0].url.params["max_price"] == "200"
    assert seen[0].url.params["in_stock"] == "1"
    await gateway.close()


@pytest.mark.parametrize("status", [403, 404])
async def test_laravel_order_denied_or_absent(status, settings):
    client = httpx.AsyncClient(
        base_url="https://backend.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(status)),
    )
    gateway = LaravelGateway(settings, client)
    with pytest.raises(OrderUnavailable):
        await gateway.order("DEMO-1001", "owner")
    await gateway.close()


async def test_laravel_owner_checked_on_both_sides(settings):
    order = MockGateway().order_data[0].model_dump(mode="json")
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": order})

    client = httpx.AsyncClient(
        base_url="https://backend.example", transport=httpx.MockTransport(handle)
    )
    gateway = LaravelGateway(settings, client)
    with pytest.raises(OrderUnavailable):
        await gateway.order("DEMO-1001", "intruder")
    assert seen[0].headers["X-Customer-ID"] == "intruder"
    assert seen[0].url.path.endswith("DEMO-1001")
    await gateway.close()


@pytest.mark.parametrize(
    "status,payload",
    [(302, {}), (500, {}), (200, {"items": []}), (200, {"data": [{"unexpected": "field"}]})],
)
async def test_laravel_bad_contract_or_error(settings, status, payload):
    client = httpx.AsyncClient(
        base_url="https://backend.example",
        transport=httpx.MockTransport(lambda request: httpx.Response(status, json=payload)),
    )
    gateway = LaravelGateway(settings, client)
    with pytest.raises(BackendUnavailable):
        await gateway.products(Search())
    await gateway.close()


def test_order_number_must_be_literal_and_not_substring():
    assert order_number_from_message("1001", "رقم الطلب ١٠٠١") == "1001"
    assert order_number_from_message("1001", "رقم الطلب 21001") is None
    assert order_number_from_message("DEMO-1001", "demo-1001") == "DEMO-1001"
    assert order_number_from_message("../../../orders", "../../../orders") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/product",
        "http://demo.example/product",
        "https://user:pass@demo.example/product",
        "https://demo.example.evil.example/product",
        "https://demo.example:8443/product",
    ],
)
def test_unsafe_links_are_filtered(url):
    assert safe_url(url, ["demo.example"]) is None


def test_production_rejects_demo_or_missing_credentials():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, environment="production", ai_service_token="a" * 40)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, planner_mode="openai", ai_service_token="a" * 40)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ai_service_token="short")
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, ai_service_token="a" * 40, backend_products_path="https://evil.example"
        )
