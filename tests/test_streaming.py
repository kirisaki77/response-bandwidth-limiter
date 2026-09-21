import asyncio

import pytest

from response_bandwidth_limiter.middleware import ResponseBandwidthLimiterMiddleware
from response_bandwidth_limiter.streaming import ResponseStreamer


@pytest.mark.parametrize("more_body", [False, True])
@pytest.mark.parametrize("size", [0, 5, 20, 25])
def test_chunks_are_sent_as_soon_as_their_delay_finishes(size, more_body):
    elapsed = 0.0
    messages = []

    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    async def send(message):
        messages.append((elapsed, message))

    middleware = ResponseBandwidthLimiterMiddleware(
        None, response_streamer=ResponseStreamer(chunk_size=10, sleep_func=sleep)
    )
    asyncio.run(middleware._send_limited_body(send, b"x" * size, more_body, 10))

    expected_sizes = [min(10, size - offset) for offset in range(0, size, 10)] or [0]
    expected_times = []
    total = 0
    for part_size in expected_sizes:
        total += part_size
        expected_times.append(total / 10)
    assert [timestamp for timestamp, _ in messages] == pytest.approx(expected_times)
    assert [len(message["body"]) for _, message in messages] == expected_sizes
    assert b"".join(message["body"] for _, message in messages) == b"x" * size
    assert [message["more_body"] for _, message in messages] == (
        [True] * (len(messages) - 1) + [more_body]
    )
