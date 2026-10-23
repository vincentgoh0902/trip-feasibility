from datetime import timedelta


async def test_put_then_get(check_deps):
    await check_deps.cache.put("test", "k1", [1, 2], timedelta(minutes=5))
    assert await check_deps.cache.get("k1") == [1, 2]


async def test_expired_entries_are_misses(check_deps):
    await check_deps.cache.put("test", "k1", {"a": 1}, timedelta(seconds=-1))
    assert await check_deps.cache.get("k1") is None


async def test_put_overwrites(check_deps):
    await check_deps.cache.put("test", "k1", "old", timedelta(minutes=5))
    await check_deps.cache.put("test", "k1", "new", timedelta(minutes=5))
    assert await check_deps.cache.get("k1") == "new"


async def test_cached_only_fetches_once(check_deps):
    calls = 0

    async def fetch():
        nonlocal calls
        calls += 1
        return {"v": calls}

    first = await check_deps.cached("test", "k2", timedelta(minutes=5), fetch)
    second = await check_deps.cached("test", "k2", timedelta(minutes=5), fetch)
    assert first == second == {"v": 1}
    assert calls == 1
