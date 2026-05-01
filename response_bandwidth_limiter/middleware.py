import asyncio
import logging
import signal
import threading
from ipaddress import ip_address
from types import FrameType
from typing import Any, AsyncIterator, Callable, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .ip_manager import IPManager
from .models import PolicyDecision, Rule
from .policy import MatchedPolicy, PolicyEvaluator
from .shutdown import ShutdownCoordinator, ShutdownMode
from .storage import StorageUnavailableError
from .streaming import ResponseStreamer, StreamingAbortedError
from .util import _find_configured_handler_name


logger = logging.getLogger(__name__)

class ResponseBandwidthLimiterMiddleware:
    chunk_size = 8192

    def __init__(
        self,
        app: ASGIApp,
        policy_evaluator: Optional[PolicyEvaluator] = None,
        ip_manager: Optional[IPManager] = None,
        response_streamer: Optional[ResponseStreamer] = None,
        shutdown_coordinator: Optional[ShutdownCoordinator] = None,
        install_signal_handlers: bool = True,
    ):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
        """
        self.app = app
        self.policy_evaluator = policy_evaluator or PolicyEvaluator()
        self.ip_manager = ip_manager
        self.response_streamer = response_streamer or ResponseStreamer(chunk_size=self.chunk_size, sleep_func=asyncio.sleep)
        self.shutdown_coordinator = shutdown_coordinator or ShutdownCoordinator()
        self.install_signal_handlers = install_signal_handlers
        self._signal_lock = threading.Lock()
        self._signal_handler_installed = False
        self._original_sigint_handler: Any = None

    async def _yield_limited_chunks(
        self,
        chunk: bytes,
        max_rate: int,
        abort_check: Callable[[], bool] | None = None,
        poll_check: Callable[[], bool] | None = None,
    ) -> AsyncIterator[bytes]:
        async for part in self.response_streamer.yield_limited_chunks(
            chunk,
            max_rate,
            abort_check=abort_check,
            poll_check=poll_check,
        ):
            yield part

    def _handle_sigint(self, signum: int, frame: FrameType | None) -> None:
        next_mode = ShutdownMode.ABORT if self.shutdown_coordinator.is_shutting_down else ShutdownMode.DRAIN
        self.shutdown_coordinator.begin_shutdown(next_mode)

        with self._signal_lock:
            original_handler = self._original_sigint_handler

        if original_handler in (None, signal.SIG_IGN):
            return
        if original_handler == signal.SIG_DFL:
            signal.default_int_handler(signum, frame)
            return
        if original_handler is self._handle_sigint:
            return

        original_handler(signum, frame)

    def _install_signal_handler(self) -> None:
        if not self.install_signal_handlers:
            return
        if threading.current_thread() is not threading.main_thread():
            return

        with self._signal_lock:
            if self._signal_handler_installed:
                return
            self._original_sigint_handler = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle_sigint)
            self._signal_handler_installed = True

    def _restore_signal_handler(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return

        with self._signal_lock:
            if not self._signal_handler_installed:
                return
            signal.signal(signal.SIGINT, self._original_sigint_handler)
            self._signal_handler_installed = False
            self._original_sigint_handler = None

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

    def _get_client_identifier(
        self,
        request: Request,
        trust_proxy_headers: bool = False,
    ) -> str:
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

    def _get_client_ip(self, request: Request, trust_proxy_headers: bool = False) -> str | None:
        if trust_proxy_headers:
            forwarded_ip = self._extract_valid_ip(request.headers.get("x-forwarded-for"))
            if forwarded_ip is not None:
                return forwarded_ip

            real_ip = self._extract_valid_ip(request.headers.get("x-real-ip"))
            if real_ip is not None:
                return real_ip

        client = getattr(request, "client", None)
        if client and getattr(client, "host", None):
            return self._extract_valid_ip(str(client.host))

        scope_client = request.scope.get("client")
        if scope_client:
            return self._extract_valid_ip(str(scope_client[0]))

        return None

    def _get_limiter(self, app: Any) -> Any:
        app_state = getattr(app, "state", None)
        if app_state is None:
            return None
        return getattr(app_state, "response_bandwidth_limiter", None)

    async def _evaluate_policy_rules(
        self,
        handler_name: str,
        rules: list[Rule],
        scope_identifiers: dict[str, str],
    ) -> Optional[MatchedPolicy]:
        return await self.policy_evaluator.evaluate(scope_identifiers, handler_name, rules)

    def _resolve_scope_identifiers(self, request: Request, rules: list[Rule], limiter: Any) -> dict[str, str]:
        scope_identifiers: dict[str, str] = {}
        trust_proxy_headers = getattr(limiter, "trusted_proxy_headers", False)

        for rule in rules:
            scope_name = rule.scope
            if scope_name in scope_identifiers:
                continue

            if scope_name == "ip":
                scope_identifiers[scope_name] = self._get_client_ip(request, trust_proxy_headers) or "unknown"
                continue

            if scope_name == "default":
                scope_identifiers[scope_name] = self._get_client_identifier(request, trust_proxy_headers)
                continue

            resolver = getattr(limiter, "_get_scope_resolver", None)
            if not callable(resolver):
                raise ValueError(f"Cannot resolve a resolver getter for scope {scope_name!r}.")

            scope_resolver = resolver(scope_name)
            if scope_resolver is None:
                raise ValueError(f"scope {scope_name!r} is not registered.")

            try:
                resolved = scope_resolver(request)
            except Exception:
                logger.warning(
                    "Scope resolver %r raised an exception. Falling back to the real client IP.",
                    scope_name,
                    exc_info=True,
                )
                scope_identifiers[scope_name] = self._get_client_ip(request, trust_proxy_headers) or "unknown"
                continue

            str_value = str(resolved) if resolved is not None else ""
            if not str_value.strip():
                logger.warning(
                    "Scope resolver %r returned an empty value. Falling back to the real client IP.",
                    scope_name,
                )
                scope_identifiers[scope_name] = self._get_client_ip(request, trust_proxy_headers) or "unknown"
            else:
                scope_identifiers[scope_name] = str_value

        return scope_identifiers

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

    def _build_shutdown_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Server shutting down",
                "detail": "The server is shutting down and cannot accept new requests for routes managed by the limiter.",
            },
        )

    def _build_backend_unavailable_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Rate limit backend unavailable",
                "detail": "The request policy backend is unavailable and the limiter is configured to fail closed.",
            },
        )

    def _build_blocked_ip_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=403,
            content={
                "error": "IP blocked",
                "detail": "The client IP is blocked.",
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
        return _find_configured_handler_name(routes, request.scope, path, configured_names)

    async def _send_limited_body(
        self,
        send: Send,
        body: bytes,
        more_body: bool,
        max_rate: int,
        abort_check: Callable[[], bool] | None = None,
        poll_check: Callable[[], bool] | None = None,
    ) -> None:
        pending_chunk: Optional[bytes] = None
        async for limited_chunk in self._yield_limited_chunks(
            body,
            max_rate,
            abort_check=abort_check,
            poll_check=poll_check,
        ):
            if pending_chunk is not None:
                await send({"type": "http.response.body", "body": pending_chunk, "more_body": True})
            pending_chunk = limited_chunk

        if pending_chunk is None:
            await send({"type": "http.response.body", "body": body, "more_body": more_body})
            return

        await send({"type": "http.response.body", "body": pending_chunk, "more_body": more_body})

    async def _handle_lifespan(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def receive_with_signal() -> Message:
            message = await receive()
            if self.install_signal_handlers and message["type"] == "lifespan.startup":
                self._install_signal_handler()
            return message

        try:
            await self.app(scope, receive_with_signal, send)
        finally:
            if self.install_signal_handlers:
                self._restore_signal_handler()

            limiter = self._get_limiter(scope.get("app", self.app))
            if limiter is not None and hasattr(limiter, "close"):
                await limiter.close()
            else:
                evaluator_storage = getattr(self.policy_evaluator, "storage", None)
                if evaluator_storage is not None and callable(getattr(evaluator_storage, "close", None)):
                    await evaluator_storage.close()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return

        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        app = scope.get("app", self.app)
        limiter = self._get_limiter(app)
        if limiter is None:
            await self.app(scope, receive, send)
            return

        ip_manager = self.ip_manager or getattr(limiter, "ip_manager", None)
        client_ip = self._get_client_ip(request, getattr(limiter, "trusted_proxy_headers", False))
        if ip_manager is not None and client_ip is not None:
            try:
                if await ip_manager.is_blocked(client_ip):
                    response = self._build_blocked_ip_response()
                    await response(scope, receive, send)
                    return
                ip_allowed = await ip_manager.is_allowed(client_ip)
            except StorageUnavailableError:
                response = self._build_backend_unavailable_response()
                await response(scope, receive, send)
                return
        else:
            ip_allowed = False

        path = scope["path"]
        handler_name = self.get_handler_name(request, path)

        if handler_name is None:
            await self.app(scope, receive, send)
            return

        route_limit = limiter.get_limit(handler_name)
        rules = limiter.get_rules(handler_name)
        if self.shutdown_coordinator.is_shutting_down and (route_limit is not None or rules):
            response = self._build_shutdown_response()
            await response(scope, receive, send)
            return

        decision = None
        if rules:
            try:
                scope_identifiers = self._resolve_scope_identifiers(request, rules, limiter)
            except ValueError:
                logger.error(
                    "Scope resolution failed for handler %r. Returning 503.",
                    handler_name,
                    exc_info=True,
                )
                response = self._build_backend_unavailable_response()
                await response(scope, receive, send)
                return
            try:
                matched_rule = None if ip_allowed else await self._evaluate_policy_rules(handler_name, rules, scope_identifiers)
            except StorageUnavailableError:
                response = self._build_backend_unavailable_response()
                await response(scope, receive, send)
                return
            if matched_rule is not None:
                decision = matched_rule.rule.action.decide(matched_rule.retry_after)
                if decision.reject:
                    response = self._build_reject_response(decision)
                    await response(scope, receive, send)
                    return
                if decision.pre_delay > 0:
                    await asyncio.sleep(decision.pre_delay)

        max_rate = route_limit
        if decision is not None and decision.throttle_rate is not None:
            max_rate = decision.throttle_rate

        if max_rate is None:
            await self.app(scope, receive, send)
            return

        abort_check = lambda: self.shutdown_coordinator.should_abort
        poll_check = lambda: self.shutdown_coordinator.is_shutting_down

        async def send_with_limit(message: Message) -> None:
            if message["type"] != "http.response.body":
                await send(message)
                return

            body = message.get("body", b"")
            if not body:
                await send(message)
                return

            await self._send_limited_body(
                send,
                body,
                message.get("more_body", False),
                max_rate,
                abort_check=abort_check,
                poll_check=poll_check,
            )

        self.shutdown_coordinator.enter_response()
        try:
            try:
                await self.app(scope, receive, send_with_limit)
            except StreamingAbortedError:
                return
        finally:
            self.shutdown_coordinator.exit_response()
