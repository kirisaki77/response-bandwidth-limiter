from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


VALID_PERIODS = {"second": 1, "minute": 60, "hour": 3600}


def _resolve_window_seconds(period: str | timedelta) -> int:
    if isinstance(period, str):
        if period not in VALID_PERIODS:
            raise ValueError("per must be one of second, minute, hour, or a positive timedelta.")
        return VALID_PERIODS[period]

    if not isinstance(period, timedelta):
        raise TypeError("per must be a string or timedelta.")

    if period <= timedelta(0):
        raise ValueError("timedelta values for per must be greater than 0.")

    if period.microseconds != 0:
        raise ValueError("timedelta values for per must use whole seconds.")

    return int(period.total_seconds())


@dataclass(frozen=True)
class PolicyDecision:
    reject: bool = False
    reject_status: int = 429
    reject_detail: str = "Rate limit exceeded"
    retry_after: int = 0
    pre_delay: float = 0.0
    throttle_rate: int | None = None


@runtime_checkable
class ActionProtocol(Protocol):
    @property
    def priority(self) -> int:
        ...

    @property
    def sort_key(self) -> int | float:
        ...

    def to_dict(self) -> dict[str, Any]:
        ...

    def decide(self, retry_after: int) -> PolicyDecision:
        ...


@dataclass(frozen=True)
class Throttle:
    bytes_per_sec: int

    def __post_init__(self) -> None:
        if not isinstance(self.bytes_per_sec, int):
            raise TypeError("bytes_per_sec must be an integer.")
        if self.bytes_per_sec <= 0:
            raise ValueError("bytes_per_sec must be greater than 0.")

    @property
    def priority(self) -> int:
        return 2

    @property
    def sort_key(self) -> int:
        return self.bytes_per_sec

    def to_dict(self) -> dict[str, Any]:
        return {"type": "throttle", "bytes_per_sec": self.bytes_per_sec}

    def decide(self, retry_after: int) -> PolicyDecision:
        return PolicyDecision(retry_after=retry_after, throttle_rate=self.bytes_per_sec)


@dataclass(frozen=True)
class Reject:
    status_code: int = 429
    detail: str = "Rate limit exceeded"

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int):
            raise TypeError("status_code must be an integer.")
        if self.status_code < 400:
            raise ValueError("status_code must be at least 400.")
        if self.status_code > 599:
            raise ValueError("status_code must be at most 599.")
        if not isinstance(self.detail, str):
            raise TypeError("detail must be a string.")

    @property
    def priority(self) -> int:
        return 0

    @property
    def sort_key(self) -> int:
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {"type": "reject", "status_code": self.status_code, "detail": self.detail}

    def decide(self, retry_after: int) -> PolicyDecision:
        return PolicyDecision(
            reject=True,
            reject_status=self.status_code,
            reject_detail=self.detail,
            retry_after=retry_after,
        )


@dataclass(frozen=True)
class Delay:
    seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.seconds, (int, float)):
            raise TypeError("seconds must be a number.")
        if self.seconds <= 0:
            raise ValueError("seconds must be greater than 0.")

    @property
    def priority(self) -> int:
        return 1

    @property
    def sort_key(self) -> float:
        return -self.seconds

    def to_dict(self) -> dict[str, Any]:
        return {"type": "delay", "seconds": self.seconds}

    def decide(self, retry_after: int) -> PolicyDecision:
        return PolicyDecision(retry_after=retry_after, pre_delay=float(self.seconds))


Action = ActionProtocol


@dataclass(frozen=True)
class Rule:
    count: int
    per: str | timedelta
    action: Action
    scope: str = "ip"

    def __post_init__(self) -> None:
        if not isinstance(self.count, int):
            raise TypeError("count must be an integer.")
        if self.count <= 0:
            raise ValueError("count must be greater than 0.")
        _resolve_window_seconds(self.per)
        if not isinstance(self.scope, str):
            raise TypeError("scope must be a string.")
        normalized_scope = self.scope.strip()
        if not normalized_scope:
            raise ValueError("scope must be a non-empty string.")
        object.__setattr__(self, "scope", normalized_scope)
        if not isinstance(self.action, ActionProtocol):
            raise TypeError("action must implement ActionProtocol.")

    @property
    def window_seconds(self) -> int:
        return _resolve_window_seconds(self.per)