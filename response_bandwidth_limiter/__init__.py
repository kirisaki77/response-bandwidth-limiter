from importlib import import_module

from .backend import CounterBackend, InMemoryBackend
from .middleware import ResponseBandwidthLimiterMiddleware
from .limiter import ResponseBandwidthLimiter
from .models import Action, ActionProtocol, Delay, PolicyDecision, Reject, Rule, Throttle
from .shutdown import ShutdownMode
from .util import get_endpoint_name, get_route_path

__all__ = [
    "Action",
    "ActionProtocol",
    "CounterBackend",
    "Delay",
    "get_endpoint_name",
    "get_route_path",
    "InMemoryBackend",
    "PolicyDecision",
    "Reject",
    "RedisBackend",
    "ResponseBandwidthLimiter",
    "ResponseBandwidthLimiterMiddleware",
    "Rule",
    "ShutdownMode",
    "Throttle",
]


def __getattr__(name: str):
    if name != "RedisBackend":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    try:
        return import_module(".redis_backend", __name__).RedisBackend
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("redis"):
            raise ImportError(
                "RedisBackend requires the optional redis dependency. Install it with `pip install response-bandwidth-limiter[redis]`."
            ) from exc
        raise


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
