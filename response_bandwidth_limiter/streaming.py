import asyncio
from typing import AsyncIterator


class ResponseStreamer:
    def __init__(self, chunk_size: int = 8192, sleep_func=asyncio.sleep):
        if chunk_size <= 0:
            raise ValueError("chunk_size は1以上である必要があります。")
        self.chunk_size = chunk_size
        self._sleep_func = sleep_func

    async def yield_limited_chunks(self, chunk: bytes, max_rate: int) -> AsyncIterator[bytes]:
        if max_rate <= 0:
            raise ValueError("max_rate は1以上である必要があります。")
        effective_chunk_size = max(1, min(self.chunk_size, max_rate))
        for index in range(0, len(chunk), effective_chunk_size):
            part = chunk[index:index + effective_chunk_size]
            await self._sleep_func(len(part) / max_rate)
            yield part