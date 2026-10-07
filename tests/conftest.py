import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TOKEN = "test-service-token-0123456789-abcdefghijklmnopqrstuvwxyz"


@pytest.fixture
def settings():
    return Settings(_env_file=None, ai_service_token=TOKEN, requests_per_minute=100)


@pytest.fixture
def headers():
    return {"Authorization": f"Bearer {TOKEN}", "X-Session-Subject": "test-browser-session"}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
