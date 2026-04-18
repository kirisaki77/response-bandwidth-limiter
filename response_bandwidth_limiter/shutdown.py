import asyncio
import threading
from enum import Enum


class ShutdownMode(str, Enum):
    DRAIN = "drain"
    ABORT = "abort"


class ShutdownCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._shutting_down = False
        self._mode: ShutdownMode | None = None
        self._in_flight = 0

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    @property
    def mode(self) -> ShutdownMode | None:
        with self._lock:
            return self._mode

    @property
    def should_abort(self) -> bool:
        with self._lock:
            return self._shutting_down and self._mode is ShutdownMode.ABORT

    @property
    def should_flush(self) -> bool:
        with self._lock:
            return self._shutting_down and self._mode is ShutdownMode.DRAIN

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return self._in_flight

    def begin_shutdown(self, mode: ShutdownMode) -> None:
        with self._lock:
            if self._shutting_down:
                if self._mode is ShutdownMode.DRAIN and mode is ShutdownMode.ABORT:
                    self._mode = ShutdownMode.ABORT
                return

            self._shutting_down = True
            self._mode = mode

    def enter_response(self) -> None:
        with self._lock:
            self._in_flight += 1

    def exit_response(self) -> None:
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1

    async def wait_until_drained(self, timeout: float | None = None) -> bool:
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout

        while True:
            with self._lock:
                if self._in_flight == 0:
                    return True

            if deadline is not None and loop.time() >= deadline:
                return False

            await asyncio.sleep(0.01)

    def reset(self) -> None:
        with self._lock:
            self._shutting_down = False
            self._mode = None
            self._in_flight = 0