import math
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .backend import CounterBackend, InMemoryBackend
from .models import Rule


@dataclass(frozen=True)
class MatchedPolicy:
    rule: Rule
    retry_after: int


@dataclass(frozen=True)
class _CandidateAction:
    rule: Rule
    retry_after: int
    order: int

    @property
    def sort_tuple(self) -> tuple[int, int | float, int]:
        return (self.rule.action.priority, self.rule.action.sort_key, self.order)


class PolicyEvaluator:
    def __init__(
        self,
        backend: CounterBackend | None = None,
        time_provider: Callable[[], float] | None = None,
        max_counters: int = 10000,
    ):
        if backend is not None and not isinstance(backend, CounterBackend):
            raise TypeError("backend は CounterBackend を実装している必要があります。")
        self._backend = backend or InMemoryBackend(time_provider=time_provider, max_counters=max_counters)

    @property
    def backend(self) -> CounterBackend:
        return self._backend

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        counters = getattr(self._backend, "request_counters", None)
        if counters is None:
            return {}
        return dict(counters)

    async def evaluate(self, request_key: str, handler_name: str, rules: List[Rule]) -> Optional[MatchedPolicy]:
        matched_actions: List[_CandidateAction] = []

        for index, rule in enumerate(rules):
            hit_result = await self._backend.record_hit(request_key, handler_name, index, rule.window_seconds)
            if hit_result.hit_count > rule.count:
                retry_after = self._retry_after_seconds(
                    hit_result.oldest_timestamp,
                    hit_result.current_timestamp,
                    rule.window_seconds,
                )
                matched_actions.append(_CandidateAction(rule=rule, retry_after=retry_after, order=index))

        selected = self._select_rule_action(matched_actions)
        if selected is None:
            return None

        rule, retry_after = selected
        return MatchedPolicy(rule=rule, retry_after=retry_after)

    async def cleanup_expired(self, active_rules: Dict[str, List[Rule]]) -> None:
        await self._backend.cleanup_expired(active_rules)

    def _select_rule_action(self, matched_actions: List[_CandidateAction]) -> Optional[Tuple[Rule, int]]:
        if not matched_actions:
            return None

        selected = min(matched_actions, key=lambda item: item.sort_tuple)
        return selected.rule, selected.retry_after

    def _retry_after_seconds(self, oldest_timestamp: float | None, now: float, window_seconds: int) -> int:
        if oldest_timestamp is None:
            return 1
        retry_after = window_seconds - (now - oldest_timestamp)
        return max(1, math.ceil(retry_after))
