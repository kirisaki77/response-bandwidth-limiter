"""SIGINT registration and restoration for graceful shutdown."""

import signal
import threading
from types import FrameType
from typing import Any

from .shutdown import ShutdownCoordinator, ShutdownMode


class ShutdownSignalHandler:
    def __init__(self, shutdown_coordinator: ShutdownCoordinator):
        self.shutdown_coordinator = shutdown_coordinator
        self._signal_lock = threading.Lock()
        self._signal_handler_installed = False
        self._original_sigint_handler: Any = None

    def handle_sigint(self, signum: int, frame: FrameType | None) -> None:
        next_mode = ShutdownMode.ABORT if self.shutdown_coordinator.is_shutting_down else ShutdownMode.DRAIN
        self.shutdown_coordinator.begin_shutdown(next_mode)

        with self._signal_lock:
            original_handler = self._original_sigint_handler

        if original_handler in (None, signal.SIG_IGN):
            return
        if original_handler == signal.SIG_DFL:
            signal.default_int_handler(signum, frame)
            return
        # Bound method objects are recreated on attribute access.
        if original_handler == self.handle_sigint:
            return

        original_handler(signum, frame)

    def install(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        with self._signal_lock:
            if self._signal_handler_installed:
                return
            self._original_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self.handle_sigint)
            self._signal_handler_installed = True

    def restore(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        with self._signal_lock:
            if not self._signal_handler_installed:
                return
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._signal_handler_installed = False
            self._original_sigint_handler = None
