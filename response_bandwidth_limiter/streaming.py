import asyncio
from typing import Any, AsyncIterator

from starlette.responses import StreamingResponse


class ResponseStreamer:
    def __init__(self, chunk_size: int = 8192, sleep_func=asyncio.sleep):
        self.chunk_size = chunk_size
        self._sleep_func = sleep_func

    async def iterate_in_chunks(self, body: bytes) -> AsyncIterator[bytes]:
        for index in range(0, len(body), self.chunk_size):
            yield body[index:index + self.chunk_size]

    async def yield_limited_chunks(self, chunk: bytes, max_rate: int) -> AsyncIterator[bytes]:
        effective_chunk_size = max(1, min(self.chunk_size, max_rate))
        for index in range(0, len(chunk), effective_chunk_size):
            part = chunk[index:index + effective_chunk_size]
            if not part:
                continue
            await self._sleep_func(len(part) / max_rate)
            yield part

    def build_streaming_response(self, response: Any, iterator: Any) -> StreamingResponse:
        streaming_response = StreamingResponse(
            iterator,
            status_code=response.status_code,
            media_type=response.media_type,
            background=response.background,
        )
        streaming_response.raw_headers = list(response.raw_headers)
        return streaming_response