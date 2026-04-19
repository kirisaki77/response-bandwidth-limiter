import logging
import multiprocessing

import pytest

from response_bandwidth_limiter.models import Reject, Rule
from response_bandwidth_limiter.storage import InMemoryStorage, ManagerStorage, warn_if_storage_requires_caution


@pytest.mark.asyncio
async def test_in_memory_storage_supports_get_set_incr_delete_and_ttl():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])

    await storage.set("alpha", "value", expire=1)
    assert await storage.get("alpha") == "value"

    assert await storage.incr("count", expire=2) == 1
    assert await storage.incr("count", expire=2) == 2

    now[0] = 3.0
    assert await storage.get("alpha") is None
    assert await storage.get("count") is None

    await storage.set("beta", "value")
    await storage.delete("beta")
    assert await storage.get("beta") is None


@pytest.mark.asyncio
async def test_in_memory_storage_record_hit_is_exact_sliding_window():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])

    first = await storage.record_hit("client-a", "download", 0, 10)
    now[0] = 5.0
    second = await storage.record_hit("client-a", "download", 0, 10)
    now[0] = 11.0
    third = await storage.record_hit("client-a", "download", 0, 10)

    assert first.hit_count == 1
    assert second.hit_count == 2
    assert third.hit_count == 2


@pytest.mark.asyncio
async def test_manager_storage_supports_basic_operations():
    manager = multiprocessing.Manager()
    storage = ManagerStorage.from_manager(manager)

    try:
        await storage.set("alpha", "value", expire=10)
        assert await storage.get("alpha") == "value"
        assert await storage.incr("count", expire=10) == 1
        await storage.delete("alpha")
        assert await storage.get("alpha") is None
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_manager_storage_cleanup_handler_counters_removes_approximate_keys():
    manager = multiprocessing.Manager()
    storage = ManagerStorage.from_manager(manager, time_provider=lambda: 1.0)

    try:
        await storage.record_hit("client-a", "download", 0, 10)
        storage.cleanup_handler_counters("download")
        assert await storage.get("__rbl_counter__:download:0:client-a:0") is None
    finally:
        manager.shutdown()


@pytest.mark.asyncio
async def test_in_memory_storage_evicts_expired_keys_when_max_keys_reached():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0], max_keys=3)

    await storage.set("a", 1, expire=1)
    await storage.set("b", 2, expire=1)
    await storage.set("c", 3, expire=1)

    now[0] = 2.0
    await storage.set("d", 4)

    assert await storage.get("a") is None
    assert await storage.get("b") is None
    assert await storage.get("c") is None
    assert await storage.get("d") == 4


@pytest.mark.asyncio
async def test_in_memory_storage_evicts_lru_keys_when_max_keys_reached():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0], max_keys=3)

    await storage.set("a", 1)
    now[0] = 1.0
    await storage.set("b", 2)
    now[0] = 2.0
    await storage.set("c", 3)
    now[0] = 3.0
    await storage.set("d", 4)

    assert await storage.get("a") is None
    assert await storage.get("d") == 4


@pytest.mark.asyncio
async def test_manager_storage_cleanup_orphaned_counters_removes_stale_keys():
    manager = multiprocessing.Manager()
    storage = ManagerStorage.from_manager(manager, time_provider=lambda: 1.0)

    try:
        await storage.record_hit("client-a", "download", 0, 10)
        await storage.record_hit("client-a", "upload", 0, 10)

        all_keys = list(storage._shared_dict.keys())
        download_counters = [k for k in all_keys if str(k).startswith("__rbl_counter__:download:")]
        upload_counters = [k for k in all_keys if str(k).startswith("__rbl_counter__:upload:")]
        assert len(download_counters) > 0
        assert len(upload_counters) > 0

        storage.cleanup_orphaned_counters({"download": [Rule(count=1, per="second", action=Reject())]})

        all_keys = list(storage._shared_dict.keys())
        download_counters = [k for k in all_keys if str(k).startswith("__rbl_counter__:download:")]
        upload_counters = [k for k in all_keys if str(k).startswith("__rbl_counter__:upload:")]
        assert len(download_counters) > 0
        assert len(upload_counters) == 0
    finally:
        manager.shutdown()


def test_warn_if_storage_requires_caution_logs_expected_messages(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    monkeypatch.setenv("WEB_CONCURRENCY", "2")

    in_memory = InMemoryStorage()
    warn_if_storage_requires_caution(in_memory)

    manager = multiprocessing.Manager()
    try:
        manager_storage = ManagerStorage.from_manager(manager)
        warn_if_storage_requires_caution(manager_storage)
    finally:
        manager.shutdown()

    assert "InMemoryStorage is process-local" in caplog.text
    assert "ManagerStorage is experimental" in caplog.text


@pytest.mark.asyncio
async def test_in_memory_storage_cleanup_orphaned_counters_removes_stale_entries():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])

    await storage.record_hit("client-a", "download", 0, 10)
    await storage.record_hit("client-a", "upload", 0, 10)

    assert len(storage.request_counters) == 2

    storage.cleanup_orphaned_counters({"download": [Rule(count=1, per="second", action=Reject())]})

    counters = storage.request_counters
    assert any(key[1] == "download" for key in counters)
    assert not any(key[1] == "upload" for key in counters)


@pytest.mark.asyncio
async def test_in_memory_storage_cleanup_orphaned_counters_removes_expired_counters():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0])

    await storage.record_hit("client-a", "download", 0, 1)
    assert len(storage.request_counters) == 1

    now[0] = 10.0
    storage.cleanup_orphaned_counters({"download": [Rule(count=1, per="second", action=Reject())]})

    assert len(storage.request_counters) == 0


@pytest.mark.asyncio
async def test_in_memory_storage_evicts_oldest_counters_when_max_reached():
    now = [0.0]
    storage = InMemoryStorage(time_provider=lambda: now[0], max_counters=2)

    await storage.record_hit("client-a", "handler-a", 0, 60)
    now[0] = 1.0
    await storage.record_hit("client-b", "handler-b", 0, 60)
    now[0] = 2.0
    await storage.record_hit("client-c", "handler-c", 0, 60)

    assert len(storage.request_counters) <= 2
    counters = storage.request_counters
    assert not any(key[1] == "handler-a" for key in counters)