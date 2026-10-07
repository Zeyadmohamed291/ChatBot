from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

Language = Literal["ar", "en"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=300)
    session_id: UUID | None = None
    locale: Language = "ar"
    current_store_id: str | None = Field(default=None, max_length=64)


class Principal(StrictModel):
    subject: str
    customer_id: str | None = None


class Decision(StrictModel):
    """All keys required (nullable where appropriate) for strict OpenAI tool schemas."""

    action: Literal["products", "stores", "faq", "order", "conversation"]
    language: Language
    query: str = Field(max_length=300)
    min_price: float | None = Field(ge=0, le=10000000)
    max_price: float | None = Field(ge=0, le=10000000)
    order_number: str | None = Field(max_length=40)
    question: Literal["none", "budget", "recipient", "category"]
    conversation_kind: Literal[
        "greeting", "thanks", "goodbye", "capabilities", "action", "unsupported", "human"
    ]

    @model_validator(mode="after")
    def price_range(self):
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.min_price > self.max_price
        ):
            raise ValueError("Invalid price range")
        return self


class Search(StrictModel):
    query: str = Field(default="", max_length=300)
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    limit: int = Field(default=3, ge=1, le=7)


class Store(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    name_ar: str = Field(min_length=1, max_length=150)
    name_en: str = Field(min_length=1, max_length=150)
    description_ar: str = Field(max_length=1000)
    description_en: str = Field(max_length=1000)
    url: HttpUrl
    active: bool


class Product(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    store_id: str = Field(min_length=1, max_length=64)
    name_ar: str = Field(min_length=1, max_length=200)
    name_en: str = Field(min_length=1, max_length=200)
    description_ar: str = Field(max_length=2000)
    description_en: str = Field(max_length=2000)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    original_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    in_stock: bool
    active: bool
    store_active: bool
    url: HttpUrl
    image_url: HttpUrl | None = None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def valid_discount(self):
        if self.original_price is not None and self.original_price < self.price:
            raise ValueError("Original price cannot be below selling price")
        return self


class FAQ(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    question_ar: str = Field(min_length=1, max_length=300)
    question_en: str = Field(min_length=1, max_length=300)
    answer_ar: str = Field(min_length=1, max_length=4000)
    answer_en: str = Field(min_length=1, max_length=4000)
    active: bool


class Order(StrictModel):
    order_number: str = Field(min_length=1, max_length=40)
    customer_id: str = Field(min_length=1, max_length=64)
    status: Literal[
        "processing",
        "synced",
        "shipped",
        "delivered",
        "cancelled",
        "sync_error",
        "refund_pending",
        "refunded",
    ]
    tracking_url: HttpUrl | None = None
    updated_at: AwareDatetime


class ProductCard(StrictModel):
    id: str
    store_id: str
    name: str
    description: str
    price: Decimal
    original_price: Decimal | None
    currency: str
    url: str
    image_url: str | None
    updated_at: AwareDatetime


class StoreCard(StrictModel):
    id: str
    name: str
    description: str
    url: str


class OrderCard(StrictModel):
    order_number: str
    status: str
    status_label: str
    tracking_url: str | None
    updated_at: AwareDatetime


class Handoff(StrictModel):
    requested: bool = False
    url: str | None = None


class ChatResponse(StrictModel):
    request_id: str
    session_id: str
    language: Language
    answer: str
    product_cards: list[ProductCard] = Field(default_factory=list)
    store_cards: list[StoreCard] = Field(default_factory=list)
    order: OrderCard | None = None
    faq_ids: list[str] = Field(default_factory=list)
    quick_replies: list[str] = Field(default_factory=list, max_length=4)
    handoff: Handoff = Field(default_factory=Handoff)
    requires_login: bool = False
    requires_order_number: bool = False
    demo: bool = False
    degraded: bool = False
    elapsed_ms: int = 0


class Session(StrictModel):
    id: str
    owner: str
    language: Language = "ar"
    history: list[dict[str, str]] = Field(default_factory=list)
    awaiting_order_number: bool = False
    expires_at: float

    @field_validator("history")
    @classmethod
    def limit_history(cls, value):
        if len(value) > 40:
            raise ValueError("Too many history messages")
        return value
