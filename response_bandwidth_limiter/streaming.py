import asyncio
from collections.abc import Callable
from typing import AsyncIterator


class StreamingAbortedError(RuntimeError):
    pass


class ResponseStreamer:
    abort_poll_interval = 0.1

    def __init__(self, chunk_size: int = 8192, sleep_func=asyncio.sleep):
        if chunk_size <= 0:
            raise ValueError("chunk_size は1以上である必要があります。")
        self.chunk_size = chunk_size
        self._sleep_func = sleep_func

    def _check_abort(self, abort_check: Callable[[], bool] | None) -> None:
        if abort_check is not None and abort_check():
            raise StreamingAbortedError("レスポンス送信が中断されました。")

    async def _sleep_with_abort_check(
        self,
        duration: float,
        abort_check: Callable[[], bool] | None,
        poll_check: Callable[[], bool] | None = None,
    ) -> None:
        if duration <= 0:
            return

        if abort_check is None:
            await self._sleep_func(duration)
            return

        if poll_check is None or not poll_check():
            await self._sleep_func(duration)
            self._check_abort(abort_check)
            return

        remaining = duration
        while remaining > 0:
            sleep_duration = min(self.abort_poll_interval, remaining)
            await self._sleep_func(sleep_duration)
            remaining -= sleep_duration
            self._check_abort(abort_check)

    async def yield_limited_chunks(
        self,
        chunk: bytes,
        max_rate: int,
        abort_check: Callable[[], bool] | None = None,
        poll_check: Callable[[], bool] | None = None,
    ) -> AsyncIterator[bytes]:
        if max_rate <= 0:
            raise ValueError("max_rate は1以上である必要があります。")
        effective_chunk_size = max(1, min(self.chunk_size, max_rate))
        for index in range(0, len(chunk), effective_chunk_size):
            self._check_abort(abort_check)
            part = chunk[index:index + effective_chunk_size]
            await self._sleep_with_abort_check(len(part) / max_rate, abort_check, poll_check=poll_check)
            self._check_abort(abort_check)
            yield part