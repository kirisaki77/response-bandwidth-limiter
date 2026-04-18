import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

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
    def __init__(self, time_provider: Callable[[], float] | None = None, max_counters: int = 10000):
        if max_counters <= 0:
            raise ValueError("max_counters は1以上である必要があります。")
        self._lock = threading.RLock()
        self._time_provider = time_provider or time.monotonic
        self._request_counters: Dict[Tuple[str, str, int], Deque[float]] = {}
        self._max_counters = max_counters

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        with self._lock:
            return dict(self._request_counters)

    def evaluate(self, request_key: str, handler_name: str, rules: List[Rule]) -> Optional[MatchedPolicy]:
        with self._lock:
            now = self._time_provider()
            matched_actions: List[_CandidateAction] = []

            for index, rule in enumerate(rules):
                history = self._record_rule_hit(request_key, handler_name, index, rule, now)
                if len(history) > rule.count:
                    retry_after = self._retry_after_seconds(history, now, rule.window_seconds)
                    matched_actions.append(_CandidateAction(rule=rule, retry_after=retry_after, order=index))

            selected = self._select_rule_action(matched_actions)
            if selected is None:
                return None

            rule, retry_after = selected
            return MatchedPolicy(rule=rule, retry_after=retry_after)

    def _record_rule_hit(
        self,
        request_key: str,
        handler_name: str,
        rule_index: int,
        rule: Rule,
        now: float,
    ) -> Deque[float]:
        counter_key = (request_key, handler_name, rule_index)
        history = self._request_counters.get(counter_key)
        if history is None:
            self._evict_if_needed()
            history = deque()
            self._request_counters[counter_key] = history

        self._cleanup_counter(history, now, rule.window_seconds)
        history.append(now)
        return history

    def _evict_if_needed(self) -> None:
        if len(self._request_counters) < self._max_counters:
            return

        empty_keys = [key for key, history in self._request_counters.items() if not history]
        for key in empty_keys:
            self._request_counters.pop(key, None)

        if len(self._request_counters) < self._max_counters:
            return

        trim_by = max(1, self._max_counters // 10)
        target_size = max(0, self._max_counters - trim_by)
        overflow = len(self._request_counters) - target_size
        oldest_keys = sorted(
            self._request_counters,
            key=lambda key: self._request_counters[key][-1] if self._request_counters[key] else float("-inf"),
        )
        for key in oldest_keys[:overflow]:
            self._request_counters.pop(key, None)

    def _cleanup_counter(self, history: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while history and history[0] <= threshold:
            history.popleft()

    def cleanup_expired(self, active_rules: Dict[str, List[Rule]]) -> None:
        with self._lock:
            now = self._time_provider()
            stale_keys: List[Tuple[str, str, int]] = []

            for counter_key, history in self._request_counters.items():
                _, handler_name, rule_index = counter_key
                rules = active_rules.get(handler_name)
                if rules is None or rule_index >= len(rules):
                    stale_keys.append(counter_key)
                    continue

                self._cleanup_counter(history, now, rules[rule_index].window_seconds)
                if not history:
                    stale_keys.append(counter_key)

            for counter_key in stale_keys:
                self._request_counters.pop(counter_key, None)

    def _select_rule_action(self, matched_actions: List[_CandidateAction]) -> Optional[Tuple[Rule, int]]:
        if not matched_actions:
            return None

        selected = min(matched_actions, key=lambda item: item.sort_tuple)
        return selected.rule, selected.retry_after

    def _retry_after_seconds(self, history: Deque[float], now: float, window_seconds: int) -> int:
        if not history:
            return 1
        retry_after = window_seconds - (now - history[0])
        return max(1, math.ceil(retry_after))
