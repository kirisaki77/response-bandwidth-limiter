import asyncio
from ipaddress import ip_address
from typing import Any, AsyncIterator, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .models import PolicyDecision, Rule
from .policy import MatchedPolicy, PolicyEvaluator
from .streaming import ResponseStreamer

class ResponseBandwidthLimiterMiddleware:
    chunk_size = 8192

    def __init__(
        self,
        app: ASGIApp,
        policy_evaluator: Optional[PolicyEvaluator] = None,
        response_streamer: Optional[ResponseStreamer] = None,
    ):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
        """
        self.app = app
        self.policy_evaluator = policy_evaluator or PolicyEvaluator()
        self.response_streamer = response_streamer or ResponseStreamer(chunk_size=self.chunk_size, sleep_func=asyncio.sleep)

    def _get_limit_name(self, route: Any, endpoint: Any, path: str, configured_names: set[str]) -> Optional[str]:
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

    def _find_handler_name(self, routes: list[Any], scope: Scope, path: str, configured_names: set[str]) -> Optional[str]:
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

    def _extract_valid_ip(self, raw_value: Optional[str]) -> Optional[str]:
        if raw_value is None:
            return None

        for candidate in raw_value.split(","):
            normalized = candidate.strip()
            if not normalized:
                continue
            try:
                ip_address(normalized)
            except ValueError:
                continue
            return normalized

        return None

    def _get_request_key(
        self,
        request: Request,
        key_func: Optional[Callable[[Request], Any]],
        trust_proxy_headers: bool = False,
    ) -> str:
        if callable(key_func):
            return str(key_func(request))

        if trust_proxy_headers:
            forwarded_ip = self._extract_valid_ip(request.headers.get("x-forwarded-for"))
            if forwarded_ip is not None:
                return forwarded_ip

            real_ip = self._extract_valid_ip(request.headers.get("x-real-ip"))
            if real_ip is not None:
                return real_ip

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

    def _evaluate_policy_rules(
        self,
        request: Request,
        handler_name: str,
        rules: list[Rule],
        key_func: Any,
        trust_proxy_headers: bool,
    ) -> Optional[MatchedPolicy]:
        request_key = self._get_request_key(request, key_func, trust_proxy_headers)
        return self.policy_evaluator.evaluate(request_key, handler_name, rules)

    def _build_reject_response(self, decision: PolicyDecision) -> JSONResponse:
        headers = {"Retry-After": str(decision.retry_after)}
        return JSONResponse(
            status_code=decision.reject_status,
            headers=headers,
            content={
                "error": "Rate Limit Exceeded",
                "detail": decision.reject_detail,
            },
        )

    def _collect_active_rules(self, limiter: Any) -> dict[str, list[Rule]]:
        active_rules: dict[str, list[Rule]] = {}
        for name in limiter.configured_names:
            rules = limiter.get_rules(name)
            if rules:
                active_rules[name] = rules
        return active_rules

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

    async def _send_limited_body(self, send: Send, body: bytes, more_body: bool, max_rate: int) -> None:
        pending_chunk: Optional[bytes] = None
        async for limited_chunk in self._yield_limited_chunks(body, max_rate):
            if pending_chunk is not None:
                await send({"type": "http.response.body", "body": pending_chunk, "more_body": True})
            pending_chunk = limited_chunk

        if pending_chunk is None:
            await send({"type": "http.response.body", "body": body, "more_body": more_body})
            return

        await send({"type": "http.response.body", "body": pending_chunk, "more_body": more_body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        app = scope.get("app", self.app)
        limiter = self._get_limiter(app)
        if limiter is None:
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        handler_name = self.get_handler_name(request, path)

        if handler_name is None:
            await self.app(scope, receive, send)
            return

        active_rules = self._collect_active_rules(limiter)
        self.policy_evaluator.cleanup_expired(active_rules)

        decision = None
        rules = active_rules.get(handler_name, [])
        if rules:
            matched_rule = self._evaluate_policy_rules(
                request,
                handler_name,
                rules,
                limiter.key_func,
                getattr(limiter, "trusted_proxy_headers", False),
            )
            if matched_rule is not None:
                decision = matched_rule.rule.action.decide(matched_rule.retry_after)
                if decision.reject:
                    response = self._build_reject_response(decision)
                    await response(scope, receive, send)
                    return
                if decision.pre_delay > 0:
                    await asyncio.sleep(decision.pre_delay)

        max_rate = limiter.get_limit(handler_name)
        if decision is not None and decision.throttle_rate is not None:
            max_rate = decision.throttle_rate

        if max_rate is None:
            await self.app(scope, receive, send)
            return

        async def send_with_limit(message: Message) -> None:
            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")
            if not body:
                await send(message)
                return

            await self._send_limited_body(send, body, message.get("more_body", False), max_rate)

        await self.app(scope, receive, send_with_limit)
