from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Match
import asyncio
import math
import time
from collections import deque
from typing import Deque, Dict, Any, List, Optional, AsyncIterator, Tuple
from .errors import ResponseBandwidthLimitExceeded
from .decorator import endpoint_bandwidth_limits
from .models import Delay, Reject, Rule, Throttle

class ResponseBandwidthLimiterMiddleware(BaseHTTPMiddleware):
    chunk_size = 8192

    def __init__(self, app: Any):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
        """
        super().__init__(app)
        self.endpoint_bandwidth_limits = {}
        self.endpoint_bandwidth_policies = {}
        self.request_counters: Dict[Tuple[str, str, int], Deque[float]] = {}
        
    def get_routes(self) -> List[Any]:
        """アプリケーションからルート情報を取得"""
        return getattr(self.app, "routes", [])

    def _get_limit_name(self, route: Any, endpoint: Any, path: str, configured_names: Any) -> Optional[str]:
        endpoint_name = getattr(endpoint, "__name__", None)
        if endpoint_name in configured_names:
            return endpoint_name

        route_name = getattr(route, "name", None)
        if route_name in configured_names:
            return route_name

        route_path = getattr(route, "path", path).strip("/")
        if route_path in configured_names:
            return route_path

        if endpoint_name:
            for suffix in ["_response", "_endpoint"]:
                if endpoint_name.endswith(suffix):
                    base_name = endpoint_name[:-len(suffix)]
                    if base_name in configured_names:
                        return base_name

        return None

    def _find_handler_name(self, routes: List[Any], scope: Dict[str, Any], path: str, configured_names: Any) -> Optional[str]:
        for route in routes:
            if not hasattr(route, "matches"):
                continue

            match, child_scope = route.matches(scope)
            if match != Match.FULL:
                continue

            endpoint = child_scope.get("endpoint", getattr(route, "endpoint", None))
            handler_name = self._get_limit_name(route, endpoint, path, configured_names)
            if handler_name is not None:
                return handler_name

            nested_routes = getattr(route, "routes", None)
            if nested_routes:
                nested_scope = scope.copy()
                nested_scope.update(child_scope)
                handler_name = self._find_handler_name(nested_routes, nested_scope, path, configured_names)
                if handler_name is not None:
                    return handler_name

        return None

    async def _iterate_in_chunks(self, body: bytes):
        for index in range(0, len(body), self.chunk_size):
            yield body[index:index + self.chunk_size]

    async def _yield_limited_chunks(self, chunk: bytes, max_rate: int) -> AsyncIterator[bytes]:
        effective_chunk_size = max(1, min(self.chunk_size, max_rate))
        for index in range(0, len(chunk), effective_chunk_size):
            part = chunk[index:index + effective_chunk_size]
            if not part:
                continue
            await asyncio.sleep(len(part) / max_rate)
            yield part

    def _build_streaming_response(self, response: Any, iterator: Any) -> StreamingResponse:
        streaming_response = StreamingResponse(
            iterator,
            status_code=response.status_code,
            media_type=response.media_type,
            background=response.background,
        )
        streaming_response.raw_headers = list(response.raw_headers)
        return streaming_response

    def _get_request_key(self, request: Request, key_func: Any) -> str:
        if callable(key_func):
            return str(key_func(request))

        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return client.host

        scope_client = request.scope.get("client")
        if scope_client:
            return str(scope_client[0])

        return "unknown"

    def _get_combined_limits_and_policies(self, app: Any) -> Tuple[Dict[str, int], Dict[str, List[Rule]], Any]:
        combined_limits = self.endpoint_bandwidth_limits.copy()
        combined_limits.update(endpoint_bandwidth_limits)
        combined_policies = self.endpoint_bandwidth_policies.copy()
        key_func = None
        app_state = getattr(app, "state", None)

        if app_state:
            if hasattr(app_state, "response_bandwidth_limits"):
                combined_limits.update(app_state.response_bandwidth_limits)
            if hasattr(app_state, "response_bandwidth_policies"):
                combined_policies.update(app_state.response_bandwidth_policies)
            if hasattr(app_state, "response_bandwidth_limiter"):
                limiter = app_state.response_bandwidth_limiter
                if hasattr(limiter, "routes"):
                    combined_limits.update(limiter.routes)
                if hasattr(limiter, "policies"):
                    combined_policies.update(limiter.policies)
                key_func = getattr(limiter, "key_func", None)

        return combined_limits, combined_policies, key_func

    def _cleanup_counter(self, counter_key: Tuple[str, str, int], history: Deque[float], now: float, window_seconds: int) -> None:
        threshold = now - window_seconds
        while history and history[0] <= threshold:
            history.popleft()

    def _record_rule_hit(self, request_key: str, handler_name: str, rule_index: int, rule: Rule, now: float) -> Tuple[Deque[float], int]:
        counter_key = (request_key, handler_name, rule_index)
        history = self.request_counters.get(counter_key)
        if history is None:
            history = deque()
            self.request_counters[counter_key] = history
        self._cleanup_counter(counter_key, history, now, rule.window_seconds)
        history.append(now)
        return history, counter_key[2]

    def _priority_for_action(self, action: Any) -> int:
        if isinstance(action, Reject):
            return 0
        if isinstance(action, Delay):
            return 1
        return 2

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

    def _evaluate_policy_rules(self, request: Request, handler_name: str, rules: List[Rule], key_func: Any) -> Optional[Tuple[Rule, int]]:
        request_key = self._get_request_key(request, key_func)
        now = time.monotonic()
        matched_actions: List[Tuple[int, int, Rule, int]] = []

        for index, rule in enumerate(rules):
            history, rule_index = self._record_rule_hit(request_key, handler_name, index, rule, now)
            if len(history) > rule.count:
                retry_after = self._retry_after_seconds(history, now, rule.window_seconds)
                matched_actions.append((self._priority_for_action(rule.action), rule_index, rule, retry_after))

        return self._select_rule_action(matched_actions)

    def _build_reject_response(self, rule: Rule, retry_after: int) -> JSONResponse:
        action = rule.action
        headers = {"Retry-After": str(retry_after)}
        return JSONResponse(
            status_code=action.status_code,
            headers=headers,
            content={
                "error": "Rate Limit Exceeded",
                "detail": action.detail,
            },
        )
        
    def get_handler_name(self, request: Request, path: str) -> Optional[str]:
        """
        パスに一致するハンドラー名を取得
        
        Args:
            request: リクエストオブジェクト
            path: リクエストパス
            
        Returns:
            エンドポイント名（存在する場合）
        """
        # リクエストからアプリを取得
        app = request.scope.get("app", self.app)
        
        combined_limits, combined_policies, _ = self._get_combined_limits_and_policies(app)
        configured_names = set(combined_limits) | set(combined_policies)

        # ルートを探索
        routes = getattr(app, "routes", [])
        return self._find_handler_name(routes, request.scope, path, configured_names)

    async def dispatch(self, request: Request, call_next):
        """リクエストに対して帯域制限を適用"""
        # リクエストからアプリを取得
        app = request.scope.get("app", self.app)
        
        combined_limits, combined_policies, key_func = self._get_combined_limits_and_policies(app)
        path = request.scope["path"]
        handler_name = self.get_handler_name(request, path)

        if handler_name is None:
            return await call_next(request)

        matched_rule = self._evaluate_policy_rules(request, handler_name, combined_policies.get(handler_name, []), key_func)
        if matched_rule is not None:
            rule, retry_after = matched_rule
            if isinstance(rule.action, Reject):
                return self._build_reject_response(rule, retry_after)
            if isinstance(rule.action, Delay):
                await asyncio.sleep(rule.action.seconds)

        max_rate = combined_limits.get(handler_name, None)
        if matched_rule is not None and isinstance(matched_rule[0].action, Throttle):
            max_rate = matched_rule[0].action.bytes_per_sec

        if max_rate is None:
            return await call_next(request)
            
        response = await call_next(request)

        async def limited_iterator(iterator):
            async for chunk in iterator:
                async for limited_chunk in self._yield_limited_chunks(chunk, max_rate):
                    yield limited_chunk

        # レスポンス本文を追加で連結せず、常にストリーミングで制限する
        if hasattr(response, "body_iterator"):
            return self._build_streaming_response(response, limited_iterator(response.body_iterator))

        if hasattr(response, "body") and response.body is not None:
            return self._build_streaming_response(response, limited_iterator(self._iterate_in_chunks(response.body)))

        if hasattr(response, "streaming"):
            response.streaming = limited_iterator(response.streaming)

        return response
