import asyncio

import pytest

from response_bandwidth_limiter import Delay, ShutdownMode
from response_bandwidth_limiter.middleware import ResponseBandwidthLimiterMiddleware
from response_bandwidth_limiter.streaming import StreamingAbortedError


@pytest.mark.parametrize("seconds", [float("nan"), float("inf"), -float("inf")])
def test_delay_rejects_non_finite_seconds(seconds):
    with pytest.raises(ValueError, match="finite"):
        Delay(seconds=seconds)


@pytest.mark.parametrize("initial_mode", [None, ShutdownMode.DRAIN])
def test_abort_interrupts_policy_delay_and_cleans_up_sleep(monkeypatch, initial_mode):
    async def run():
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def sleep(seconds):
            assert seconds == 3600
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned_up.set()

        monkeypatch.setattr("response_bandwidth_limiter.middleware.asyncio.sleep", sleep)
        middleware = ResponseBandwidthLimiterMiddleware(None)
        if initial_mode is not None:
            middleware.shutdown_coordinator.begin_shutdown(initial_mode)
        task = asyncio.create_task(middleware._sleep_for_policy_delay(3600))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            middleware.shutdown_coordinator.begin_shutdown(ShutdownMode.ABORT)
            with pytest.raises(StreamingAbortedError):
                await asyncio.wait_for(task, timeout=1)
            assert cleaned_up.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_cancelled_policy_delay_cleans_up_sleep(monkeypatch):
    async def run():
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def sleep(seconds):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned_up.set()

        monkeypatch.setattr("response_bandwidth_limiter.middleware.asyncio.sleep", sleep)
        middleware = ResponseBandwidthLimiterMiddleware(None)
        task = asyncio.create_task(middleware._sleep_for_policy_delay(3600))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cleaned_up.is_set()
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
