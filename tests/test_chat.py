import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.errors import BackendUnavailable, PlannerUnavailable
from app.gateway import MockGateway
from app.main import create_app
from app.models import Decision
from app.planner import Planned


def send(client, headers, message, **extra):
    return client.post("/v1/chat", headers=headers, json={"message": message, **extra})


def decision(**updates):
    values = dict(
        action="order",
        language="en",
        query="",
        min_price=None,
        max_price=None,
        order_number=None,
        question="none",
        conversation_kind="capabilities",
    )
    values.update(updates)
    return Decision(**values)


class FixedPlanner:
    def __init__(self, value):
        self.value = value

    async def plan(self, *args):
        return Planned(self.value)

    async def close(self):
        pass


def test_greeting_and_language_switch(client, headers):
    first = send(client, headers, "مرحبا").json()
    assert first["language"] == "ar"
    assert len(first["quick_replies"]) == 4
    second = send(client, headers, "Hello", session_id=first["session_id"]).json()
    assert second["language"] == "en"
    assert second["session_id"] == first["session_id"]
    assert second["quick_replies"] == []
    assert second["demo"] is True


def test_recommendations_respect_budget_not_current_store(client, headers):
    response = send(client, headers, "عطر تحت 160", current_store_id="demo-store-7")
    assert response.status_code == 200
    cards = response.json()["product_cards"]
    assert cards and all(float(c["price"]) <= 160 for c in cards)
    assert all(c["store_id"] == "demo-store-1" for c in cards)
    assert cards[0]["original_price"] == "180"


def test_vague_gift_has_options_and_question(client, headers):
    body = send(client, headers, "عايز هدية").json()
    assert body["product_cards"]
    assert "لمين" in body["answer"]


def test_preferences_carried_to_follow_up(client, headers):
    first = send(client, headers, "Recommend a perfume").json()
    next_turn = send(client, headers, "Under 160", session_id=first["session_id"]).json()
    assert next_turn["product_cards"]
    assert all(
        c["store_id"] == "demo-store-1" and float(c["price"]) <= 160
        for c in next_turn["product_cards"]
    )


def test_all_stores_and_faq_no_login(client, headers):
    stores = send(client, headers, "اختار متجر").json()
    assert len(stores["store_cards"]) == 7
    faq = send(client, headers, "Shipping and delivery").json()
    assert faq["faq_ids"] == ["demo-shipping"]
    assert not faq["requires_login"]


def test_order_flow_requests_number_before_any_gateway_call(settings, headers):
    gateway = MockGateway()
    gateway.order = AsyncMock(wraps=gateway.order)
    with TestClient(create_app(settings, gateway=gateway)) as client:
        first = send(client, headers, "فين طلبي؟").json()
        assert first["requires_order_number"]
        assert not first["requires_login"]
        gateway.order.assert_not_called()
        second = send(client, headers, "DEMO-1001", session_id=first["session_id"]).json()
        assert second["requires_login"]
        assert second["language"] == "ar"
        gateway.order.assert_not_called()


def test_authenticated_order_not_saved_in_model_context(client, headers):
    headers["X-Customer-ID"] = "demo-customer"
    body = send(client, headers, "Where is order DEMO-1001?").json()
    assert body["order"]["status"] == "shipped"
    assert "customer_id" not in body["order"]
    stored = client.app.state.sessions.data[body["session_id"]]
    assert "shipped" not in json.dumps(stored.history).lower()


@pytest.mark.parametrize("number", ["DEMO-2002", "DEMO-9999"])
def test_unowned_and_unknown_orders_identical(client, headers, number):
    headers["X-Customer-ID"] = "demo-customer"
    body = send(client, headers, f"order {number}").json()
    assert body["order"] is None
    assert (
        body["answer"]
        == "I can't display this order for your account. Please check the number and sign in with the account that placed it."
    )
    assert body["handoff"]["requested"]


def test_model_cannot_invent_order_number(settings, headers):
    gateway = MockGateway()
    gateway.order = AsyncMock(wraps=gateway.order)
    planner = FixedPlanner(decision(order_number="DEMO-1001"))
    headers["X-Customer-ID"] = "demo-customer"
    with TestClient(create_app(settings, gateway=gateway, planner=planner)) as client:
        response = send(client, headers, "Where is my order?").json()
        assert response["requires_order_number"]
        gateway.order.assert_not_called()


def test_even_gateway_must_return_matching_owner(settings, headers):
    gateway = MockGateway()
    gateway.order = AsyncMock(return_value=gateway.order_data[1])
    headers["X-Customer-ID"] = "demo-customer"
    with TestClient(create_app(settings, gateway=gateway)) as client:
        result = send(client, headers, "Order DEMO-1001").json()
        assert result["order"] is None


def test_products_validated_independently_of_backend(settings, headers):
    gateway = MockGateway()
    original = gateway.product_data[0]
    gateway.products = AsyncMock(
        return_value=[
            original.model_copy(update={"in_stock": False}),
            original.model_copy(update={"id": "x", "url": "https://evil.example/item"}),
            original.model_copy(update={"id": "y", "store_active": False}),
            original,
            original,
        ]
    )
    with TestClient(create_app(settings, gateway=gateway)) as client:
        result = send(client, headers, "عطر").json()
        assert len(result["product_cards"]) == 1
        assert result["product_cards"][0]["id"] == original.id


def test_missing_faq_and_backend_failure_are_honest(settings, headers):
    gateway = MockGateway()
    gateway.faqs = AsyncMock(return_value=[])
    with TestClient(create_app(settings, gateway=gateway)) as client:
        result = send(client, headers, "What is your shipping policy?").json()
        assert result["faq_ids"] == []
        assert result["handoff"]["requested"]
        gateway.products = AsyncMock(side_effect=BackendUnavailable())
        result = send(client, headers, "Recommend perfume").json()
        assert result["degraded"] and not result["product_cards"]


def test_planner_failure_returns_fallback(settings, headers):
    planner = FixedPlanner(decision())
    planner.plan = AsyncMock(side_effect=PlannerUnavailable())
    with TestClient(create_app(settings, planner=planner)) as client:
        result = send(client, headers, "Hello").json()
        assert result["degraded"] and result["handoff"]["requested"]


def test_session_ownership_logout_and_expiry(client, headers):
    initial = send(client, headers, "Hello").json()
    sid = initial["session_id"]
    different = headers | {"X-Session-Subject": "other-browser-session"}
    assert send(client, different, "Hi", session_id=sid).status_code == 404
    logged_in = headers | {"X-Customer-ID": "demo-customer"}
    assert send(client, logged_in, "Hi", session_id=sid).status_code == 404
    assert client.delete(f"/v1/sessions/{sid}", headers=different).status_code == 404
    assert client.delete(f"/v1/sessions/{sid}", headers=headers).status_code == 204
    assert send(client, headers, "Hi", session_id=sid).status_code == 404
    new = send(client, headers, "Hi").json()["session_id"]
    client.app.state.sessions.data[new].expires_at = time.time() - 1
    assert send(client, headers, "Hi", session_id=new).status_code == 404


def test_sessions_bounded_and_same_session_cannot_overlap(client, headers):
    sid = send(client, headers, "Hi").json()["session_id"]
    client.app.state.sessions.active.add(sid)
    assert send(client, headers, "Hi", session_id=sid).status_code == 409
    client.app.state.sessions.active.clear()
    for _ in range(12):
        assert send(client, headers, "Thanks", session_id=sid).status_code == 200
    assert len(client.app.state.sessions.data[sid].history) == 16


@pytest.mark.parametrize("message", ["", "   ", "x" * 301])
def test_invalid_message(client, headers, message):
    assert send(client, headers, message).status_code == 422


def test_client_identity_cannot_be_supplied_in_body(client, headers):
    assert send(client, headers, "Hi", customer_id="victim").status_code == 422
    assert send(client, {}, "Hi").status_code == 401
    assert send(client, headers | {"Authorization": "Bearer wrong"}, "Hi").status_code == 401
    assert send(client, {"Authorization": headers["Authorization"]}, "Hi").status_code == 400


def test_read_only_and_handoff(settings, headers):
    settings.whatsapp_phone = "966500000000"
    with TestClient(create_app(settings)) as client:
        action = send(client, headers, "Cancel my order DEMO-1001").json()
        assert action["order"] is None
        assert "cancellations" in action["answer"]
        result = send(client, headers, "I need a human for order DEMO-1001").json()
        assert result["handoff"]["url"].startswith("https://wa.me/")
        assert "DEMO-1001" not in result["handoff"]["url"]
        assert client.post("/v1/orders/cancel", headers=headers, json={}).status_code == 404


def test_rate_limit_and_large_body(settings, headers):
    settings.requests_per_minute = 1
    with TestClient(create_app(settings)) as client:
        assert send(client, headers, "Hi").status_code == 200
        assert send(client, headers, "Hi").status_code == 429
        assert send(client, headers, "x" * 9000).status_code == 413
