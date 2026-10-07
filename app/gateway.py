import json
from importlib.resources import files
from typing import Protocol
from urllib.parse import quote, urlparse

import httpx
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.errors import BackendUnavailable, OrderUnavailable
from app.models import FAQ, Order, Product, Search, Store
from app.text import normalize, score


class BackendGateway(Protocol):
    async def products(self, search: Search) -> list[Product]: ...
    async def stores(self, query: str) -> list[Store]: ...
    async def faqs(self, query: str) -> list[FAQ]: ...
    async def order(self, number: str, customer_id: str) -> Order: ...
    async def close(self) -> None: ...


def safe_url(url, hosts: list[str]) -> str | None:
    if url is None:
        return None
    parsed = urlparse(str(url))
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        return None
    if parsed.hostname not in {h.lower() for h in hosts}:
        return None
    return str(url)


class MockGateway:
    """Synthetic fixtures only. Never enabled in production."""

    def __init__(self):
        data = json.loads(files("app").joinpath("data/catalog.json").read_text(encoding="utf-8"))
        self.product_data = TypeAdapter(list[Product]).validate_python(data["products"])
        self.store_data = TypeAdapter(list[Store]).validate_python(data["stores"])
        self.faq_data = TypeAdapter(list[FAQ]).validate_python(data["faqs"])
        self.order_data = TypeAdapter(list[Order]).validate_python(data["orders"])

    async def products(self, search: Search) -> list[Product]:
        ranked = []
        broad = normalize(search.query) in {
            "",
            "منتجات",
            "products",
            "هديه",
            "gift",
            "gifts",
            "هدايا",
        }
        for product in self.product_data:
            if not (product.active and product.in_stock and product.store_active):
                continue
            if search.min_price is not None and product.price < search.min_price:
                continue
            if search.max_price is not None and product.price > search.max_price:
                continue
            rank = score(
                search.query,
                " ".join(
                    [
                        product.name_ar,
                        product.name_en,
                        product.description_ar,
                        product.description_en,
                    ]
                ),
            )
            if broad or rank:
                ranked.append((rank, product))
        ranked.sort(key=lambda pair: (-pair[0], pair[1].price))
        return [p for _, p in ranked[: search.limit]]

    async def stores(self, query: str) -> list[Store]:
        ranked = [
            (score(query, " ".join([s.name_ar, s.name_en, s.description_ar, s.description_en])), s)
            for s in self.store_data
            if s.active
        ]
        ranked.sort(key=lambda pair: -pair[0])
        return [s for rank, s in ranked if not query or rank]

    async def faqs(self, query: str) -> list[FAQ]:
        ranked = [
            (score(query, f"{f.question_ar} {f.question_en}"), f) for f in self.faq_data if f.active
        ]
        ranked.sort(key=lambda pair: -pair[0])
        return [f for rank, f in ranked if rank][:2]

    async def order(self, number: str, customer_id: str) -> Order:
        for order in self.order_data:
            if order.order_number == number and order.customer_id == customer_id:
                return order
        raise OrderUnavailable()

    async def close(self):
        pass


class LaravelGateway:
    """This module is the only place to adapt Laravel's eventual response shape."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.client = client or httpx.AsyncClient(
            base_url=settings.backend_base_url,
            headers={
                "Authorization": f"Bearer {settings.backend_api_token.get_secret_value()}",
                "Accept": "application/json",
            },
            timeout=settings.backend_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    async def _get(self, path: str, *, params=None, customer_id=None):
        headers = {"X-Customer-ID": customer_id} if customer_id else None
        try:
            # Limit upstream response size, including decompressed data.
            async with self.client.stream("GET", path, params=params, headers=headers) as response:
                if customer_id and response.status_code in {403, 404}:
                    raise OrderUnavailable()
                if response.status_code != 200:
                    raise BackendUnavailable()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 1024 * 1024:
                        raise BackendUnavailable()
            payload = json.loads(body)
            if not isinstance(payload, dict) or "data" not in payload:
                raise BackendUnavailable()
            return payload["data"]
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise BackendUnavailable() from exc

    async def products(self, search: Search) -> list[Product]:
        params = {k: str(v) for k, v in search.model_dump().items() if v is not None}
        params.update({"in_stock": "1", "active": "1"})
        # No current_store_id filter: search spans all stores by agreement.
        data = await self._get(self.settings.backend_products_path, params=params)
        return self._validate(list[Product], data)

    async def stores(self, query: str) -> list[Store]:
        return self._validate(
            list[Store],
            await self._get(
                self.settings.backend_stores_path, params={"query": query, "active": "1"}
            ),
        )

    async def faqs(self, query: str) -> list[FAQ]:
        return self._validate(
            list[FAQ],
            await self._get(self.settings.backend_faq_path, params={"query": query, "limit": 2}),
        )

    async def order(self, number: str, customer_id: str) -> Order:
        path = self.settings.backend_order_path.replace("{order_number}", quote(number, safe=""))
        order = self._validate(Order, await self._get(path, customer_id=customer_id))
        # Defense in depth; Laravel must enforce this before returning any data.
        if order.customer_id != customer_id or order.order_number != number:
            raise OrderUnavailable()
        return order

    @staticmethod
    def _validate(schema, data):
        try:
            return TypeAdapter(schema).validate_python(data)
        except ValidationError as exc:
            raise BackendUnavailable() from exc

    async def close(self):
        await self.client.aclose()
