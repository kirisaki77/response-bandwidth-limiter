from importlib import import_module

from .storage import InMemoryStorage, ManagerStorage, SlidingWindowResult, Storage, StorageUnavailableError
from .middleware import ResponseBandwidthLimiterMiddleware
from .limiter import ResponseBandwidthLimiter
from .models import Action, ActionProtocol, Delay, PolicyDecision, Reject, Rule, Throttle
from .shutdown import ShutdownMode
from .util import get_endpoint_name, get_route_path

__all__ = [
    "Action",
    "ActionProtocol",
    "Delay",
    "get_endpoint_name",
    "get_route_path",
    "InMemoryStorage",
    "IPManager",
    "ManagerStorage",
    "PolicyDecision",
    "Reject",
    "RedisStorage",
    "ResponseBandwidthLimiter",
    "ResponseBandwidthLimiterMiddleware",
    "Rule",
    "ShutdownMode",
    "SlidingWindowResult",
    "Storage",
    "StorageUnavailableError",
    "Throttle",
]


def __getattr__(name: str):
    if name == "RedisStorage":
        try:
            return import_module(".redis_storage", __name__).RedisStorage
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith("redis"):
                raise ImportError(
                    "RedisStorage requires the optional redis dependency. Install it with `pip install response-bandwidth-limiter[redis]`."
                ) from exc
            raise

    if name == "IPManager":
        return import_module(".ip_manager", __name__).IPManager

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
