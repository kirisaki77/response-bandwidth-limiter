from dataclasses import dataclass
from typing import Union


VALID_PERIODS = {"second": 1, "minute": 60, "hour": 3600}


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

    def to_dict(self) -> dict:
        return {"type": "throttle", "bytes_per_sec": self.bytes_per_sec}


@dataclass(frozen=True)
class Reject:
    status_code: int = 429
    detail: str = "Rate limit exceeded"

    def __post_init__(self) -> None:
        if not isinstance(self.status_code, int):
            raise TypeError("status_code は整数である必要があります。")
        if self.status_code < 400:
            raise ValueError("status_code は400以上である必要があります。")
        if not isinstance(self.detail, str):
            raise TypeError("detail は文字列である必要があります。")

    @property
    def priority(self) -> int:
        return 0

    def to_dict(self) -> dict:
        return {"type": "reject", "status_code": self.status_code, "detail": self.detail}


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

    def to_dict(self) -> dict:
        return {"type": "delay", "seconds": self.seconds}


Action = Union[Throttle, Reject, Delay]


@dataclass(frozen=True)
class Rule:
    count: int
    per: str
    action: Action
    scope: str = "ip"

    def __post_init__(self) -> None:
        if not isinstance(self.count, int):
            raise TypeError("count は整数である必要があります。")
        if self.count <= 0:
            raise ValueError("count は1以上である必要があります。")
        if self.per not in VALID_PERIODS:
            raise ValueError("per は second, minute, hour のいずれかである必要があります。")
        if self.scope != "ip":
            raise ValueError("scope は現在 ip のみ対応しています。")
        if not isinstance(self.action, (Throttle, Reject, Delay)):
            raise TypeError("action は Throttle, Reject, Delay のいずれかである必要があります。")

    @property
    def window_seconds(self) -> int:
        return VALID_PERIODS[self.per]