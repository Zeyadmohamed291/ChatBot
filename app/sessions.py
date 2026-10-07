import asyncio
import hashlib
import hmac
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from redis.asyncio import Redis
from redis.exceptions import LockError

from app.config import Settings
from app.errors import Busy, SessionUnavailable
from app.models import Principal, Session


def owner_key(principal: Principal, secret: str) -> str:
    identity = f"{principal.subject}\0{principal.customer_id or ''}"
    return hmac.new(secret.encode(), identity.encode(), hashlib.sha256).hexdigest()


class MemorySessions:
    """Single-process development store. No transcript files are written."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.data: dict[str, Session] = {}
        self.active: set[str] = set()
        self.limits: dict[str, tuple[int, int]] = {}

    async def prune(self):
        now = time.time()
        self.data = {
            key: value
            for key, value in self.data.items()
            if value.expires_at > now or key in self.active
        }
        minute = int(now // 60)
        self.limits = {key: value for key, value in self.limits.items() if value[0] == minute}

    async def rate_allowed(self, owner: str) -> bool:
        await self.prune()
        minute = int(time.time() // 60)
        window, count = self.limits.get(owner, (minute, 0))
        if len(self.limits) >= self.settings.max_sessions and owner not in self.limits:
            return False
        count = count + 1 if window == minute else 1
        self.limits[owner] = (minute, count)
        return count <= self.settings.requests_per_minute

    @asynccontextmanager
    async def turn(self, session_id: str | None, owner: str, language: str):
        await self.prune()
        if session_id:
            value = self.data.get(session_id)
            if value is None or value.owner != owner or value.expires_at <= time.time():
                raise SessionUnavailable()
        else:
            if len(self.data) >= self.settings.max_sessions:
                raise Busy()
            session_id = str(uuid4())
            value = Session(
                id=session_id,
                owner=owner,
                language=language,
                expires_at=time.time() + self.settings.session_ttl_seconds,
            )
        if session_id in self.active:
            raise Busy()
        self.active.add(session_id)
        self.data[session_id] = value
        try:
            copy = value.model_copy(deep=True)
            yield copy
            copy.expires_at = time.time() + self.settings.session_ttl_seconds
            self.data[session_id] = copy
        finally:
            self.active.discard(session_id)

    async def delete(self, session_id: str, owner: str):
        value = self.data.get(session_id)
        if value is None or value.owner != owner or value.expires_at <= time.time():
            raise SessionUnavailable()
        if session_id in self.active:
            raise Busy()
        del self.data[session_id]

    async def ping(self):
        return True

    async def close(self):
        self.data.clear()
        self.limits.clear()


class RedisSessions:
    def __init__(self, settings: Settings, client: Redis | None = None):
        self.settings = settings
        self.client = client or Redis.from_url(
            settings.redis_url.get_secret_value(),
            decode_responses=True,
            socket_timeout=3,
            socket_connect_timeout=3,
        )

    async def prune(self):
        pass  # Redis TTL physically expires abandoned sessions, including inactive ones.

    async def rate_allowed(self, owner: str) -> bool:
        key = f"loulou:rate:{owner}:{int(time.time() // 60)}"
        count = await self.client.eval(
            "local n=redis.call('INCR',KEYS[1]); if n==1 then redis.call('EXPIRE',KEYS[1],120) end; return n",
            1,
            key,
        )
        return count <= self.settings.requests_per_minute

    @asynccontextmanager
    async def turn(self, session_id: str | None, owner: str, language: str):
        existing = session_id is not None
        session_id = session_id or str(uuid4())
        key = f"loulou:session:{session_id}"
        lock = self.client.lock(
            f"{key}:lock",
            timeout=self.settings.request_timeout_seconds + 10,
            blocking=False,
            thread_local=False,
        )
        if not await lock.acquire():
            raise Busy()
        try:
            raw = await self.client.get(key)
            if existing:
                if raw is None:
                    raise SessionUnavailable()
                session = Session.model_validate_json(raw)
                if session.owner != owner or session.expires_at <= time.time():
                    raise SessionUnavailable()
            else:
                session = Session(
                    id=session_id,
                    owner=owner,
                    language=language,
                    expires_at=time.time() + self.settings.session_ttl_seconds,
                )
            yield session
            session.expires_at = time.time() + self.settings.session_ttl_seconds
            await self.client.set(
                key, session.model_dump_json(), ex=self.settings.session_ttl_seconds
            )
        finally:
            try:
                await asyncio.shield(lock.release())
            except LockError:
                pass

    async def delete(self, session_id: str, owner: str):
        key = f"loulou:session:{session_id}"
        lock = self.client.lock(f"{key}:lock", timeout=5, blocking=False, thread_local=False)
        if not await lock.acquire():
            raise Busy()
        try:
            raw = await self.client.get(key)
            if raw is None or Session.model_validate_json(raw).owner != owner:
                raise SessionUnavailable()
            await self.client.delete(key)
        finally:
            await lock.release()

    async def ping(self):
        return await self.client.ping()

    async def close(self):
        await self.client.aclose()
