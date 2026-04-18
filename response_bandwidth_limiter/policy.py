import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from fastapi import Request

from .models import Delay, Reject, Rule, Throttle


@dataclass(frozen=True)
class MatchedPolicy:
    rule: Rule
    retry_after: int


class PolicyEvaluator:
    def __init__(self, time_provider: Callable[[], float] | None = None):
        self._time_provider = time_provider or time.monotonic
        self._request_counters: Dict[Tuple[str, str, int], Deque[float]] = {}

    @property
    def request_counters(self) -> Dict[Tuple[str, str, int], Deque[float]]:
        return self._request_counters

    def evaluate(self, request_key: str, handler_name: str, rules: List[Rule]) -> Optional[MatchedPolicy]:
        now = self._time_provider()
        matched_actions: List[Tuple[int, int, Rule, int]] = []

        for index, rule in enumerate(rules):
            history = self._record_rule_hit(request_key, handler_name, index, rule, now)
            if len(history) > rule.count:
                retry_after = self._retry_after_seconds(history, now, rule.window_seconds)
                matched_actions.append((rule.action.priority, index, rule, retry_after))

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
            history = deque()
            self._request_counters[counter_key] = history

        self._cleanup_counter(history, now, rule.window_seconds)
        history.append(now)

        if history:
            return history

        self._request_counters.pop(counter_key, None)
        return history

    def _cleanup_counter(self, history: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while history and history[0] <= threshold:
            history.popleft()

    def cleanup_expired(self, active_rules: Dict[str, List[Rule]]) -> None:
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

    def _select_rule_action(self, matched_actions: List[Tuple[int, int, Rule, int]]) -> Optional[Tuple[Rule, int]]:
        if not matched_actions:
            return None

        matched_actions.sort(key=lambda item: (item[0], item[1]))
        top_priority = matched_actions[0][0]
        same_priority = [item for item in matched_actions if item[0] == top_priority]

        if top_priority == 1:
            same_priority.sort(key=lambda item: (-item[2].action.seconds, item[1]))
        elif top_priority == 2:
            same_priority.sort(key=lambda item: (item[2].action.bytes_per_sec, item[1]))

        _, _, rule, retry_after = same_priority[0]
        return rule, retry_after

    def _retry_after_seconds(self, history: Deque[float], now: float, window_seconds: int) -> int:
        if not history:
            return 1
        retry_after = window_seconds - (now - history[0])
        return max(1, math.ceil(retry_after))
