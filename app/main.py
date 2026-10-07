import asyncio
import contextlib
import hmac
import logging
import re
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings
from app.errors import Busy, SessionUnavailable
from app.gateway import LaravelGateway, MockGateway
from app.models import ChatRequest, ChatResponse, Principal
from app.planner import DemoPlanner, OpenAIPlanner
from app.service import ChatService
from app.sessions import MemorySessions, RedisSessions, owner_key
from app.telemetry import configure_logging

logger = logging.getLogger("loulou.api")
bearer = HTTPBearer(auto_error=False)


class BodyLimitMiddleware:
    """Reject oversized JSON without loading an unbounded request into memory."""

    def __init__(self, app, limit=8192):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        try:
            if int(headers.get(b"content-length", b"0")) > self.limit:
                return await JSONResponse({"error": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
        except ValueError:
            return await JSONResponse({"error": "invalid_content_length"}, status_code=400)(
                scope, receive, send
            )
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            body.extend(event.get("body", b""))
            if len(body) > self.limit:
                return await JSONResponse({"error": "request_too_large"}, status_code=413)(
                    scope, receive, send
                )
            if not event.get("more_body", False):
                break
        consumed = False

        async def buffered_receive():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, buffered_receive, send)


def create_app(settings: Settings | None = None, *, gateway=None, planner=None, sessions=None):
    configure_logging()
    settings = settings or Settings()
    gateway = gateway or (
        MockGateway() if settings.backend_mode == "mock" else LaravelGateway(settings)
    )
    planner = planner or (
        DemoPlanner() if settings.planner_mode == "demo" else OpenAIPlanner(settings)
    )
    sessions = sessions or (
        RedisSessions(settings)
        if settings.redis_url.get_secret_value()
        else MemorySessions(settings)
    )
    service = ChatService(settings, gateway, planner)
    capacity = asyncio.Semaphore(settings.max_concurrent_requests)

    async def janitor():
        while True:
            await asyncio.sleep(30)
            await sessions.prune()

    @asynccontextmanager
    async def lifespan(app):
        await sessions.ping()
        cleanup = asyncio.create_task(janitor())
        try:
            yield
        finally:
            cleanup.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cleanup
            await asyncio.gather(planner.close(), gateway.close(), sessions.close())

    app = FastAPI(
        title="Loulou AI",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment == "development" else None,
    )
    app.add_middleware(BodyLimitMiddleware)
    app.state.settings, app.state.sessions, app.state.service = settings, sessions, service

    @app.middleware("http")
    async def metadata(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
        subject: Annotated[str | None, Header(alias="X-Session-Subject")] = None,
        customer: Annotated[str | None, Header(alias="X-Customer-ID")] = None,
    ):
        if credentials is None or not hmac.compare_digest(
            credentials.credentials.encode(), settings.ai_service_token.get_secret_value().encode()
        ):
            raise HTTPException(401, "invalid_service_credentials")
        if subject is None or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", subject):
            raise HTTPException(400, "invalid_session_subject")
        if customer is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", customer):
            raise HTTPException(400, "invalid_customer_context")
        return Principal(subject=subject, customer_id=customer)

    async def generic_error(request, code, status):
        return JSONResponse(
            {"error": code, "request_id": request.state.request_id}, status_code=status
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        result = await generic_error(request, exc.detail, exc.status_code)
        if exc.headers:
            result.headers.update(exc.headers)
        return result

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        # Do not echo submitted messages, arbitrary JSON fields or headers into errors.
        return await generic_error(request, "invalid_request", 422)

    @app.exception_handler(SessionUnavailable)
    async def invalid_session(request, exc):
        return await generic_error(request, "session_unavailable_start_new", 404)

    @app.exception_handler(Busy)
    async def busy(request, exc):
        result = await generic_error(request, "busy_retry_shortly", 409)
        result.headers["Retry-After"] = "2"
        return result

    @app.exception_handler(RedisError)
    async def storage_down(request, exc):
        return await generic_error(request, "session_store_unavailable", 503)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logger.error(
            "unexpected_error request_id=%s type=%s", request.state.request_id, type(exc).__name__
        )
        return await generic_error(request, "internal_error", 500)

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready():
        await sessions.ping()
        return {
            "status": "ready",
            "planner": settings.planner_mode,
            "backend": settings.backend_mode,
        }

    @app.post("/v1/chat", response_model=ChatResponse)
    async def chat(
        body: ChatRequest, request: Request, user: Annotated[Principal, Depends(principal)]
    ):
        owner = owner_key(user, settings.ai_service_token.get_secret_value())
        if not await sessions.rate_allowed(owner):
            raise HTTPException(429, "rate_limit_exceeded", headers={"Retry-After": "60"})
        if capacity.locked():
            raise HTTPException(503, "capacity_exceeded", headers={"Retry-After": "2"})
        async with capacity:
            async with sessions.turn(
                str(body.session_id) if body.session_id else None, owner, body.locale
            ) as session:
                return await service.respond(body, session, user, request.state.request_id)

    @app.delete("/v1/sessions/{session_id}", status_code=204)
    async def end_session(session_id: UUID, user: Annotated[Principal, Depends(principal)]):
        await sessions.delete(
            str(session_id), owner_key(user, settings.ai_service_token.get_secret_value())
        )
        return Response(status_code=204)

    return app
