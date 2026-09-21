import asyncio
import signal
import threading

import pytest

from response_bandwidth_limiter.shutdown import ShutdownCoordinator, ShutdownMode
from response_bandwidth_limiter.signals import ShutdownSignalHandler


@pytest.fixture
def signal_state(monkeypatch):
    calls = []
    original = lambda signum, frame: calls.append((signum, frame))
    state = {"handler": original, "registrations": [], "calls": calls}

    def register(signum, handler):
        assert signum == signal.SIGINT
        state["registrations"].append(handler)
        state["handler"] = handler

    monkeypatch.setattr(signal, "getsignal", lambda signum: state["handler"])
    monkeypatch.setattr(signal, "signal", register)
    return state


def test_install_restore_are_idempotent_and_forward_original(signal_state):
    original = signal_state["handler"]
    coordinator = ShutdownCoordinator()
    handler = ShutdownSignalHandler(coordinator)
    handler.install()
    handler.install()
    signal_state["handler"](signal.SIGINT, None)
    assert coordinator.mode is ShutdownMode.DRAIN
    signal_state["handler"](signal.SIGINT, None)
    assert coordinator.mode is ShutdownMode.ABORT
    assert signal_state["calls"] == [(signal.SIGINT, None)] * 2
    handler.restore()
    handler.restore()
    assert signal_state["handler"] is original
    assert len(signal_state["registrations"]) == 2


def test_signal_registration_is_skipped_outside_main_thread(signal_state):
    handler = ShutdownSignalHandler(ShutdownCoordinator())
    worker = threading.Thread(target=handler.install)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert signal_state["registrations"] == []
    handler.install()
    worker = threading.Thread(target=handler.restore)
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert len(signal_state["registrations"]) == 1
    handler.restore()
    assert len(signal_state["registrations"]) == 2


def test_existing_bound_handler_does_not_recurse(signal_state):
    coordinator = ShutdownCoordinator()
    handler = ShutdownSignalHandler(coordinator)
    signal_state["handler"] = handler.handle_sigint
    handler.install()
    handler.handle_sigint(signal.SIGINT, None)
    assert coordinator.mode is ShutdownMode.DRAIN


@pytest.mark.parametrize("original", [signal.SIG_IGN, signal.SIG_DFL])
def test_special_original_handlers(signal_state, monkeypatch, original):
    defaults = []
    signal_state["handler"] = original
    monkeypatch.setattr(signal, "default_int_handler", lambda *args: defaults.append(args))
    coordinator = ShutdownCoordinator()
    handler = ShutdownSignalHandler(coordinator)
    handler.install()
    handler.handle_sigint(signal.SIGINT, None)
    assert coordinator.mode is ShutdownMode.DRAIN
    assert defaults == ([(signal.SIGINT, None)] if original == signal.SIG_DFL else [])
    handler.restore()
    assert signal_state["handler"] == original


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_lifespan_restores_signal_and_closes_storage(signal_state, enabled, fail):
    from response_bandwidth_limiter.middleware import ResponseBandwidthLimiterMiddleware
    from response_bandwidth_limiter.policy import PolicyEvaluator
    from response_bandwidth_limiter.storage import InMemoryStorage

    class ClosingStorage(InMemoryStorage):
        close_calls = 0

        async def close(self):
            self.close_calls += 1
            await super().close()

    original = signal_state["handler"]
    storage = ClosingStorage()

    async def app(scope, receive, send):
        assert (await receive())["type"] == "lifespan.startup"
        assert (signal_state["handler"] is not original) is enabled
        if fail:
            raise RuntimeError("startup failed")

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        pass

    middleware = ResponseBandwidthLimiterMiddleware(
        app, policy_evaluator=PolicyEvaluator(storage=storage), install_signal_handlers=enabled,
    )
    if fail:
        with pytest.raises(RuntimeError, match="startup failed"):
            asyncio.run(middleware({"type": "lifespan"}, receive, send))
    else:
        asyncio.run(middleware({"type": "lifespan"}, receive, send))
    assert signal_state["handler"] is original
    assert storage.close_calls == 1
    assert len(signal_state["registrations"]) == (2 if enabled else 0)
