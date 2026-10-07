from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["development", "production"] = "development"
    planner_mode: Literal["demo", "openai"] = "demo"
    backend_mode: Literal["mock", "laravel"] = "mock"
    ai_service_token: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    openai_timeout_seconds: float = Field(default=15, gt=0, le=60)
    max_output_tokens: int = Field(default=1000, ge=256, le=4096)
    backend_base_url: str = ""
    backend_api_token: SecretStr = SecretStr("")
    backend_stores_path: str = "/api/ai/stores"
    backend_products_path: str = "/api/ai/products/search"
    backend_faq_path: str = "/api/ai/faqs/search"
    backend_order_path: str = "/api/ai/orders/{order_number}"
    backend_timeout_seconds: float = Field(default=5, gt=0, le=30)
    allowed_catalog_hosts: list[str] = ["demo.example"]
    allowed_tracking_hosts: list[str] = ["tracking.example"]
    whatsapp_phone: str = ""
    redis_url: SecretStr = SecretStr("")
    session_ttl_seconds: int = Field(default=1800, ge=60, le=86400)
    max_sessions: int = Field(default=1000, ge=1, le=100000)
    history_turns: int = Field(default=8, ge=1, le=20)
    requests_per_minute: int = Field(default=20, ge=1, le=1000)
    max_concurrent_requests: int = Field(default=20, ge=1, le=200)
    request_timeout_seconds: int = Field(default=25, ge=5, le=120)
    recommendation_limit: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def check_configuration(self):
        token = self.ai_service_token.get_secret_value()
        if len(token) < 32:
            raise ValueError("AI_SERVICE_TOKEN must contain at least 32 characters")
        if self.planner_mode == "openai":
            if not self.openai_api_key.get_secret_value() or not self.openai_model:
                raise ValueError("OpenAI mode requires OPENAI_API_KEY and OPENAI_MODEL")
        if self.backend_mode == "laravel":
            url = urlparse(self.backend_base_url)
            if url.scheme not in {"https", "http"} or not url.hostname or url.username:
                raise ValueError("BACKEND_BASE_URL must be an HTTP(S) origin")
            if url.path not in {"", "/"} or url.query or url.fragment:
                raise ValueError("BACKEND_BASE_URL must not contain a path/query")
            if not self.backend_api_token.get_secret_value():
                raise ValueError("BACKEND_API_TOKEN is required")
        for path in (
            self.backend_stores_path,
            self.backend_products_path,
            self.backend_faq_path,
            self.backend_order_path,
        ):
            if (
                not path.startswith("/")
                or path.startswith("//")
                or any(c in path for c in (":", "?", "#", ".."))
            ):
                raise ValueError("Backend paths must be relative paths on the configured origin")
        if self.backend_order_path.count("{order_number}") != 1:
            raise ValueError("BACKEND_ORDER_PATH must contain {order_number} exactly once")
        if self.whatsapp_phone and (
            not self.whatsapp_phone.isascii()
            or not self.whatsapp_phone.isdigit()
            or not 8 <= len(self.whatsapp_phone) <= 15
        ):
            raise ValueError("WHATSAPP_PHONE must be 8-15 international digits, without +")
        if self.environment == "production":
            if self.planner_mode != "openai" or self.backend_mode != "laravel":
                raise ValueError("Production requires OpenAI and Laravel modes")
            if not self.redis_url.get_secret_value():
                raise ValueError("Production requires REDIS_URL for shared ephemeral sessions")
            if not self.backend_base_url.startswith("https://"):
                raise ValueError("Production backend must use HTTPS")
            if not self.allowed_catalog_hosts or "demo.example" in self.allowed_catalog_hosts:
                raise ValueError("Configure real ALLOWED_CATALOG_HOSTS for production")
            if token.startswith("local-development"):
                raise ValueError("Replace the development service token in production")
        return self
