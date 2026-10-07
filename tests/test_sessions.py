import asyncio
import time

import fakeredis.aioredis
import pytest

from app.errors import Busy, SessionUnavailable
from app.sessions import MemorySessions, RedisSessions


@pytest.fixture(params=["memory", "redis"])
async def store(request, settings):
    if request.param == "redis":
        value = RedisSessions(settings, fakeredis.aioredis.FakeRedis(decode_responses=True))
    else:
        value = MemorySessions(settings)
    yield value
    await value.close()


async def test_session_roundtrip_ownership_delete(store):
    async with store.turn(None, "owner", "ar") as state:
        sid = state.id
        state.history.append({"role": "user", "content": "Hello"})
    async with store.turn(sid, "owner", "ar") as state:
        assert state.history[0]["content"] == "Hello"
    with pytest.raises(SessionUnavailable):
        async with store.turn(sid, "intruder", "ar"):
            pass
    with pytest.raises(SessionUnavailable):
        await store.delete(sid, "intruder")
    await store.delete(sid, "owner")
    with pytest.raises(SessionUnavailable):
        async with store.turn(sid, "owner", "ar"):
            pass


async def test_parallel_turns_are_rejected_and_cancel_releases_lock(store):
    async with store.turn(None, "owner", "ar") as state:
        sid = state.id
    entered = asyncio.Event()

    async def active_turn():
        async with store.turn(sid, "owner", "ar"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(active_turn())
    await entered.wait()
    with pytest.raises(Busy):
        async with store.turn(sid, "owner", "ar"):
            pass
    with pytest.raises(Busy):
        await store.delete(sid, "owner")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with store.turn(sid, "owner", "ar"):
        pass


async def test_rate_limit_is_per_owner(store):
    store.settings.requests_per_minute = 2
    assert await store.rate_allowed("owner")
    assert await store.rate_allowed("owner")
    assert not await store.rate_allowed("owner")
    assert await store.rate_allowed("other")


async def test_redis_expiry_and_shared_workers(settings):
    server = fakeredis.FakeServer()
    one = RedisSessions(
        settings, fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    two = RedisSessions(
        settings, fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
    )
    async with one.turn(None, "owner", "ar") as session:
        sid = session.id
    key = f"loulou:session:{sid}"
    assert 0 < await one.client.ttl(key) <= settings.session_ttl_seconds
    async with two.turn(sid, "owner", "ar") as session:
        session.language = "en"
        with pytest.raises(Busy):
            async with one.turn(sid, "owner", "ar"):
                pass
    async with one.turn(sid, "owner", "ar") as session:
        assert session.language == "en"
    # fakeredis uses a strict '< now' expiry comparison; use a past instant,
    # so this assertion does not depend on sub-millisecond clock advancement.
    await one.client.expireat(key, int(time.time()) - 1)
    with pytest.raises(SessionUnavailable):
        async with two.turn(sid, "owner", "ar"):
            pass
    await one.close()
    await two.close()


async def test_memory_bounded_and_expired_data_pruned(settings):
    settings.max_sessions = 1
    store = MemorySessions(settings)
    async with store.turn(None, "owner", "ar") as session:
        sid = session.id
    with pytest.raises(Busy):
        async with store.turn(None, "owner", "ar"):
            pass
    store.data[sid].expires_at = time.time() - 1
    await store.prune()
    assert store.data == {}
