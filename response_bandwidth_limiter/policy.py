import math
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Mapping, Optional, Tuple

from .storage import InMemoryStorage, SlidingWindowResult, Storage
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
        storage: Storage | None = None,
        time_provider: Callable[[], float] | None = None,
        max_counters: int = 10000,
    ):
        if storage is not None and not isinstance(storage, Storage):
            raise TypeError("storage must implement Storage.")
        self._storage = storage or InMemoryStorage(time_provider=time_provider, max_counters=max_counters)

    @property
    def storage(self) -> Storage:
        return self._storage

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        counters = getattr(self._storage, "request_counters", None)
        if counters is None:
            return {}
        return dict(counters)

    async def evaluate(
        self,
        scope_identifiers: Mapping[str, str],
        handler_name: str,
        rules: List[Rule],
    ) -> Optional[MatchedPolicy]:
        matched_actions: List[_CandidateAction] = []

        for index, rule in enumerate(rules):
            request_key = scope_identifiers.get(rule.scope)
            if request_key is None:
                raise ValueError(f"No identifier was resolved for scope {rule.scope!r}.")
            hit_result = await self._storage.record_hit(request_key, handler_name, index, rule.window_seconds)
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
