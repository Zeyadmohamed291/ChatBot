import asyncio
import json
import logging
import re
import time
from decimal import Decimal

from app.config import Settings
from app.errors import BackendUnavailable, OrderUnavailable, PlannerUnavailable
from app.gateway import BackendGateway, safe_url
from app.models import (
    ChatRequest,
    ChatResponse,
    Decision,
    OrderCard,
    Principal,
    ProductCard,
    Search,
    Session,
    StoreCard,
)
from app.planner import Planner
from app.replies import STATUSES, handoff, quick_replies, text
from app.text import digits, extract_number, language_of, order_number_from_message

logger = logging.getLogger("loulou.chat")


class ChatService:
    def __init__(self, settings: Settings, gateway: BackendGateway, planner: Planner):
        self.settings, self.gateway, self.planner = settings, gateway, planner

    async def respond(
        self, request: ChatRequest, session: Session, principal: Principal, request_id: str
    ) -> ChatResponse:
        start = time.monotonic()
        # An order identifier such as DEMO-1001 is not a language switch.
        identifier_reply = session.awaiting_order_number and re.fullmatch(
            r"[A-Za-z0-9-]+", digits(request.message)
        )
        language = (
            session.language
            if identifier_reply
            else language_of(
                request.message, session.language if session.history else request.locale
            )
        )
        response = ChatResponse(
            request_id=request_id,
            session_id=session.id,
            language=language,
            answer="",
            demo=self.settings.planner_mode == "demo" or self.settings.backend_mode == "mock",
        )
        usage_in = usage_out = 0
        action = "fallback"
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                if identifier_reply and extract_number(request.message):
                    decision = Decision(
                        action="order",
                        language=language,
                        query="",
                        min_price=None,
                        max_price=None,
                        order_number=extract_number(request.message),
                        question="none",
                        conversation_kind="capabilities",
                    )
                else:
                    result = await self.planner.plan(request.message, session, language)
                    decision = result.decision
                    usage_in, usage_out = result.input_tokens, result.output_tokens
                    # The planner handles Arabizi; clear Arabic/English always follows this turn.
                    decision.language = language_of(request.message, decision.language)
                    language = decision.language
                    response.language = language
                action = decision.action
                if action != "order":
                    session.awaiting_order_number = False
                await self._execute(decision, request, session, principal, response)
        except (PlannerUnavailable, BackendUnavailable, TimeoutError):
            response.answer = text("unavailable", language)
            response.handoff = handoff(self.settings.whatsapp_phone, language)
            response.degraded = True

        if not session.history:
            response.quick_replies = quick_replies(language)
        response.elapsed_ms = int((time.monotonic() - start) * 1000)
        session.language = language
        # Order details are never placed into future model context.
        saved_answer = (
            "Order request handled; never infer an order number from this note."
            if action == "order"
            else response.answer
        )
        if response.product_cards:
            saved_answer += "\n" + json.dumps(
                [c.model_dump(mode="json") for c in response.product_cards], ensure_ascii=False
            )
        session.history.extend(
            [
                {"role": "user", "content": request.message},
                {"role": "assistant", "content": saved_answer[:6000]},
            ]
        )
        session.history = session.history[-2 * self.settings.history_turns :]
        logger.info(
            json.dumps(
                {
                    "event": "chat_completed",
                    "request_id": request_id,
                    "action": action,
                    "elapsed_ms": response.elapsed_ms,
                    "input_tokens": usage_in,
                    "output_tokens": usage_out,
                    "degraded": response.degraded,
                }
            )
        )
        return response

    async def _execute(
        self,
        decision: Decision,
        request: ChatRequest,
        session: Session,
        principal: Principal,
        response: ChatResponse,
    ):
        lang = response.language
        if decision.action == "products":
            search = Search(
                query=decision.query,
                min_price=Decimal(str(decision.min_price))
                if decision.min_price is not None
                else None,
                max_price=Decimal(str(decision.max_price))
                if decision.max_price is not None
                else None,
                limit=self.settings.recommendation_limit,
            )
            products = await self.gateway.products(search)
            seen = set()
            for product in products:
                if product.id in seen or not (
                    product.active and product.store_active and product.in_stock
                ):
                    continue
                if search.min_price is not None and product.price < search.min_price:
                    continue
                if search.max_price is not None and product.price > search.max_price:
                    continue
                url = safe_url(product.url, self.settings.allowed_catalog_hosts)
                if not url:
                    continue
                seen.add(product.id)
                response.product_cards.append(
                    ProductCard(
                        id=product.id,
                        store_id=product.store_id,
                        name=product.name_ar if lang == "ar" else product.name_en,
                        description=(
                            product.description_ar if lang == "ar" else product.description_en
                        )[:400],
                        price=product.price,
                        original_price=product.original_price,
                        currency=product.currency,
                        updated_at=product.updated_at,
                        url=url,
                        image_url=safe_url(product.image_url, self.settings.allowed_catalog_hosts),
                    )
                )
                if len(response.product_cards) >= self.settings.recommendation_limit:
                    break
            response.answer = text("products" if response.product_cards else "no_products", lang)
            if decision.question != "none" and response.product_cards:
                response.answer += " " + text(decision.question, lang)
            if not response.product_cards:
                response.handoff = handoff(self.settings.whatsapp_phone, lang)

        elif decision.action == "stores":
            stores = await self.gateway.stores(decision.query)
            if not stores and decision.query:
                stores = await self.gateway.stores("")
            for store in stores:
                url = safe_url(store.url, self.settings.allowed_catalog_hosts)
                if store.active and url and store.id not in {s.id for s in response.store_cards}:
                    response.store_cards.append(
                        StoreCard(
                            id=store.id,
                            name=store.name_ar if lang == "ar" else store.name_en,
                            description=store.description_ar
                            if lang == "ar"
                            else store.description_en,
                            url=url,
                        )
                    )
            response.store_cards = response.store_cards[:7]
            response.answer = text("all_stores" if response.store_cards else "no_stores", lang)
            if not response.store_cards:
                response.handoff = handoff(self.settings.whatsapp_phone, lang)

        elif decision.action == "faq":
            faqs = [faq for faq in await self.gateway.faqs(decision.query) if faq.active][:2]
            response.faq_ids = [faq.id for faq in faqs]
            response.answer = "\n\n".join(
                faq.answer_ar if lang == "ar" else faq.answer_en for faq in faqs
            )
            if not faqs:
                response.answer = text("no_faq", lang)
                response.handoff = handoff(self.settings.whatsapp_phone, lang)

        elif decision.action == "order":
            # This guard is enforced in code, independent of model instructions.
            number = order_number_from_message(decision.order_number, request.message)
            if number is None:
                session.awaiting_order_number = True
                response.requires_order_number = True
                response.answer = text("order_number", lang)
                return
            session.awaiting_order_number = False
            if principal.customer_id is None:
                response.requires_login = True
                response.answer = text("login", lang)
                return
            try:
                order = await self.gateway.order(number, principal.customer_id)
                if order.customer_id != principal.customer_id or order.order_number != number:
                    raise OrderUnavailable()
            except OrderUnavailable:
                response.answer = text("order_unavailable", lang)
                response.handoff = handoff(self.settings.whatsapp_phone, lang)
                return
            label = STATUSES[order.status][0 if lang == "ar" else 1]
            response.order = OrderCard(
                order_number=number,
                status=order.status,
                status_label=label,
                tracking_url=safe_url(order.tracking_url, self.settings.allowed_tracking_hosts),
                updated_at=order.updated_at,
            )
            response.answer = (
                f"حالة طلبك {number}: {label}."
                if lang == "ar"
                else f"Your order {number} is: {label}."
            )

        else:
            key = decision.conversation_kind
            if key in {"human", "unsupported"}:
                response.handoff = handoff(self.settings.whatsapp_phone, lang)
                if key == "human" and not response.handoff.url:
                    key = "no_support"
            response.answer = text(key, lang)
