from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Match
import asyncio
from typing import Any, AsyncIterator, List, Optional

from .models import Delay, Reject, Rule, Throttle
from .policy import MatchedPolicy, PolicyEvaluator
from .streaming import ResponseStreamer

class ResponseBandwidthLimiterMiddleware(BaseHTTPMiddleware):
    chunk_size = 8192

    def __init__(self, app: Any):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
        """
        super().__init__(app)
        self.policy_evaluator = PolicyEvaluator()
        self.response_streamer = ResponseStreamer(chunk_size=self.chunk_size, sleep_func=asyncio.sleep)
        
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

    async def _yield_limited_chunks(self, chunk: bytes, max_rate: int) -> AsyncIterator[bytes]:
        async for part in self.response_streamer.yield_limited_chunks(chunk, max_rate):
            yield part

    def _build_streaming_response(self, response: Any, iterator: Any) -> StreamingResponse:
        return self.response_streamer.build_streaming_response(response, iterator)

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

    def _get_limiter(self, app: Any) -> Any:
        app_state = getattr(app, "state", None)
        if app_state is None:
            return None
        return getattr(app_state, "response_bandwidth_limiter", None)

    def _evaluate_policy_rules(self, request: Request, handler_name: str, rules: List[Rule], key_func: Any) -> Optional[MatchedPolicy]:
        request_key = self._get_request_key(request, key_func)
        return self.policy_evaluator.evaluate(request_key, handler_name, rules)

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
        limiter = self._get_limiter(app)
        if limiter is None:
            return None

        configured_names = limiter.configured_names

        # ルートを探索
        routes = getattr(app, "routes", [])
        return self._find_handler_name(routes, request.scope, path, configured_names)

    async def dispatch(self, request: Request, call_next):
        """リクエストに対して帯域制限を適用"""
        # リクエストからアプリを取得
        app = request.scope.get("app", self.app)
        limiter = self._get_limiter(app)
        if limiter is None:
            return await call_next(request)

        path = request.scope["path"]
        handler_name = self.get_handler_name(request, path)

        if handler_name is None:
            return await call_next(request)

        rules = limiter.get_rules(handler_name)
        self.policy_evaluator.cleanup_expired({name: limiter.get_rules(name) for name in limiter.configured_names if limiter.get_rules(name)})
        matched_rule = self._evaluate_policy_rules(request, handler_name, rules, limiter.key_func)
        if matched_rule is not None:
            rule = matched_rule.rule
            if isinstance(rule.action, Reject):
                return self._build_reject_response(rule, matched_rule.retry_after)
            if isinstance(rule.action, Delay):
                await asyncio.sleep(rule.action.seconds)

        max_rate = limiter.get_limit(handler_name)
        if matched_rule is not None and isinstance(matched_rule.rule.action, Throttle):
            max_rate = matched_rule.rule.action.bytes_per_sec

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
            return self._build_streaming_response(response, limited_iterator(self.response_streamer.iterate_in_chunks(response.body)))

        if hasattr(response, "streaming"):
            response.streaming = limited_iterator(response.streaming)

        return response
