from datetime import timedelta
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


VALID_PERIODS = {"second": 1, "minute": 60, "hour": 3600}


def _resolve_window_seconds(period: str | timedelta) -> int:
    if isinstance(period, str):
        if period not in VALID_PERIODS:
            raise ValueError("per は second, minute, hour のいずれか、または正の timedelta である必要があります。")
        return VALID_PERIODS[period]

    if not isinstance(period, timedelta):
        raise TypeError("per は文字列または timedelta である必要があります。")

    if period <= timedelta(0):
        raise ValueError("per に timedelta を指定する場合は0より大きい必要があります。")

    if period.microseconds != 0:
        raise ValueError("per に timedelta を指定する場合は1秒単位である必要があります。")

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
            raise TypeError("bytes_per_sec は整数である必要があります。")
        if self.bytes_per_sec <= 0:
            raise ValueError("bytes_per_sec は1以上である必要があります。")

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
            raise TypeError("status_code は整数である必要があります。")
        if self.status_code < 400:
            raise ValueError("status_code は400以上である必要があります。")
        if self.status_code > 599:
            raise ValueError("status_code は599以下である必要があります。")
        if not isinstance(self.detail, str):
            raise TypeError("detail は文字列である必要があります。")

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
            raise TypeError("seconds は数値である必要があります。")
        if self.seconds <= 0:
            raise ValueError("seconds は0より大きい必要があります。")

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
            raise TypeError("count は整数である必要があります。")
        if self.count <= 0:
            raise ValueError("count は1以上である必要があります。")
        _resolve_window_seconds(self.per)
        if self.scope != "ip":
            raise ValueError("scope は現在 ip のみ対応しています。")
        if not isinstance(self.action, ActionProtocol):
            raise TypeError("action は ActionProtocol を実装している必要があります。")

    @property
    def window_seconds(self) -> int:
        return _resolve_window_seconds(self.per)