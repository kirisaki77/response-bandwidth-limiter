import logging
import inspect
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional

from starlette.applications import Starlette
from starlette.requests import Request

from .ip_manager import IPManager
from .middleware import ResponseBandwidthLimiterMiddleware
from .models import Rule
from .policy import PolicyEvaluator
from .shutdown import ShutdownCoordinator, ShutdownMode
from .storage import InMemoryStorage, Storage, warn_if_storage_requires_caution
from .util import _find_configured_handler_name


logger = logging.getLogger(__name__)

ScopeResolver = Callable[[Request], Any]

class ResponseBandwidthLimiter:
    """
    レスポンス帯域幅制限と request count policy の設定を管理するクラス。
    
    Example:
        ```
        from response_bandwidth_limiter import ResponseBandwidthLimiter
        from fastapi import FastAPI, Request
        
        limiter = ResponseBandwidthLimiter()
        app = FastAPI()
        
        @app.get("/download")
        @limiter.limit(1024)  # 1024 bytes/sec
        async def download_file(request: Request):
            return FileResponse(...)

        limiter.init_app(app)
        ```
    """
    def __init__(
        self,
        trusted_proxy_headers: bool = False,
        storage: Optional[Storage] = None,
    ):
        self._lock = threading.RLock()
        self._route_limits: Dict[str, int] = {}
        self._route_policies: Dict[str, List[Rule]] = {}
        self._shutdown_coordinator = ShutdownCoordinator()
        self._storage = storage or InMemoryStorage()
        self._policy_evaluator = PolicyEvaluator(storage=self._storage)
        self._ip_manager = IPManager(storage=self._storage)
        self._scope_resolvers: Dict[str, ScopeResolver] = {}
        self._app: Starlette | None = None
        self.trusted_proxy_headers = trusted_proxy_headers
        self._storage_warning_emitted = False

    @staticmethod
    def _is_builtin_scope(scope_name: str) -> bool:
        return scope_name in {"ip", "default"}

    def _normalize_scope_name(self, scope_name: str) -> str:
        if not isinstance(scope_name, str):
            raise TypeError("scope_name must be a string.")
        normalized_scope_name = scope_name.strip()
        if not normalized_scope_name:
            raise ValueError("scope_name must be a non-empty string.")
        return normalized_scope_name

    def _is_async_scope_resolver(self, resolver: ScopeResolver) -> bool:
        if inspect.iscoroutinefunction(resolver) or inspect.isasyncgenfunction(resolver):
            return True

        call_method = getattr(resolver, "__call__", None)
        return bool(
            call_method
            and (inspect.iscoroutinefunction(call_method) or inspect.isasyncgenfunction(call_method))
        )

    def _validate_endpoint_name(self, endpoint_name: str) -> None:
        if not isinstance(endpoint_name, str) or not endpoint_name:
            raise ValueError("endpoint_name must be a non-empty string.")

    def _validate_rate(self, rate: int, *, decorator_context: bool = False) -> None:
        if not isinstance(rate, int):
            if decorator_context:
                raise TypeError("Bandwidth limit must be an integer. Example: @limiter.limit(1024)")
            raise TypeError("Bandwidth limit must be an integer.")
        if rate <= 0:
            if decorator_context:
                raise ValueError("Bandwidth limit must be greater than 0. Remove the setting to disable it.")
            raise ValueError("Bandwidth limit must be greater than 0.")

    def _validate_rules(self, rules: List[Rule]) -> None:
        if not isinstance(rules, list):
            raise TypeError("rules must be a list of Rule instances.")
        if not rules:
            raise ValueError("rules must contain at least one item.")
        if not all(isinstance(rule, Rule) for rule in rules):
            raise TypeError("rules can only contain Rule instances.")
        unknown_scopes = [rule.scope for rule in rules if not self._is_builtin_scope(rule.scope) and rule.scope not in self._scope_resolvers]
        if unknown_scopes:
            unique_scopes = ", ".join(sorted(set(unknown_scopes)))
            raise ValueError(
                f"Unknown scope(s): {unique_scopes}. Call register_scope_resolver() first."
            )

    @property
    def routes(self) -> Mapping[str, int]:
        with self._lock:
            return dict(self._route_limits)

    @property
    def policies(self) -> Mapping[str, List[Rule]]:
        with self._lock:
            return {name: list(rules) for name, rules in self._route_policies.items()}

    @property
    def configured_names(self) -> set[str]:
        with self._lock:
            return set(self._route_limits) | set(self._route_policies)

    @property
    def shutdown_coordinator(self) -> ShutdownCoordinator:
        return self._shutdown_coordinator

    @property
    def storage(self) -> Storage:
        return self._storage

    @property
    def ip_manager(self) -> IPManager:
        return self._ip_manager

    def register_scope_resolver(self, scope_name: str, resolver: ScopeResolver) -> None:
        normalized_scope_name = self._normalize_scope_name(scope_name)
        if self._is_builtin_scope(normalized_scope_name):
            raise ValueError(f"scope {normalized_scope_name!r} is reserved.")
        if not callable(resolver):
            raise TypeError("resolver must be callable.")
        if self._is_async_scope_resolver(resolver):
            raise TypeError("resolver must be synchronous.")
        with self._lock:
            if normalized_scope_name in self._scope_resolvers:
                raise ValueError(f"scope {normalized_scope_name!r} is already registered.")
            self._scope_resolvers[normalized_scope_name] = resolver

    def _get_scope_resolver(self, scope_name: str) -> Optional[ScopeResolver]:
        normalized_scope_name = self._normalize_scope_name(scope_name)
        with self._lock:
            return self._scope_resolvers.get(normalized_scope_name)

    @property
    def scope_resolvers(self) -> Mapping[str, ScopeResolver]:
        with self._lock:
            return dict(self._scope_resolvers)

    def resolve_handler_identifier(self, request: Request) -> str | None:
        app = request.scope.get("app", self._app)
        if app is None:
            raise ValueError(
                "resolve_handler_identifier() requires init_app() or a request bound to an application."
            )

        routes = getattr(app, "routes", [])
        path = str(request.scope.get("path", ""))
        return _find_configured_handler_name(routes, request.scope, path, self.configured_names)

    def get_limit(self, endpoint_name: str) -> int | None:
        with self._lock:
            return self._route_limits.get(endpoint_name)

    def get_rules(self, endpoint_name: str) -> List[Rule]:
        with self._lock:
            return list(self._route_policies.get(endpoint_name, []))

    def update_route(self, endpoint_name: str, rate: int) -> None:
        self._validate_endpoint_name(endpoint_name)
        self._validate_rate(rate)
        with self._lock:
            self._route_limits[endpoint_name] = rate

    def remove_route(self, endpoint_name: str) -> None:
        with self._lock:
            self._route_limits.pop(endpoint_name, None)

    def update_policy(self, endpoint_name: str, rules: List[Rule]) -> None:
        self._validate_endpoint_name(endpoint_name)
        self._validate_rules(rules)
        with self._lock:
            self._route_policies[endpoint_name] = list(rules)
            active_rules = {name: list(configured_rules) for name, configured_rules in self._route_policies.items()}
        self._storage.cleanup_handler_counters(endpoint_name)
        self._storage.cleanup_orphaned_counters(active_rules)

    def remove_policy(self, endpoint_name: str) -> None:
        with self._lock:
            self._route_policies.pop(endpoint_name, None)
            active_rules = {name: list(configured_rules) for name, configured_rules in self._route_policies.items()}
        self._storage.cleanup_handler_counters(endpoint_name)
        self._storage.cleanup_orphaned_counters(active_rules)

    def begin_shutdown(self, mode: ShutdownMode) -> None:
        self._shutdown_coordinator.begin_shutdown(mode)

    async def shutdown(self, mode: ShutdownMode, timeout: float | None = None) -> bool:
        self.begin_shutdown(mode)
        return await self._shutdown_coordinator.wait_until_drained(timeout=timeout)

    async def close(self) -> None:
        await self._storage.close()

    async def block_ip(self, ip: str, duration: int | None = None) -> None:
        await self._ip_manager.block_ip(ip, duration=duration)

    async def unblock_ip(self, ip: str) -> None:
        await self._ip_manager.unblock_ip(ip)

    async def is_blocked(self, ip: str) -> bool:
        return await self._ip_manager.is_blocked(ip)

    async def allow_ip(self, ip: str) -> None:
        await self._ip_manager.allow_ip(ip)

    async def remove_allow(self, ip: str) -> None:
        await self._ip_manager.remove_allow(ip)

    async def is_allowed(self, ip: str) -> bool:
        return await self._ip_manager.is_allowed(ip)
        
    def limit(self, rate: int) -> Callable:
        """
        帯域幅を制限する装飾子
        
        Args:
            rate: 制限する速度（bytes/sec）
        
        Returns:
            装飾子関数
            
        Example:
            @app.get("/video")
            @limiter.limit(2048)  # 2048 bytes/sec
            async def stream_video(request: Request):
                return StreamingResponse(...)
        """
        self._validate_rate(rate, decorator_context=True)
            
        def decorator(func):
            self.update_route(func.__name__, rate)
            return func
            
        return decorator

    def limit_rules(self, rules: List[Rule]) -> Callable:
        """
        request count ベースのポリシーを設定する装飾子

        Args:
            rules: Rule の配列

        Returns:
            装飾子関数
        """
        self._validate_rules(rules)

        def decorator(func):
            self.update_policy(func.__name__, rules)
            return func

        return decorator
        
    def init_app(self, app: Starlette, install_signal_handlers: bool = True) -> None:
        """
        アプリケーションにリミッターを登録する
        
        Args:
            app: FastAPIまたはStarletteアプリケーション
        """
        if not self._storage_warning_emitted:
            warn_if_storage_requires_caution(self._storage)
            self._storage_warning_emitted = True

        self._app = app
        app.state.response_bandwidth_limiter = self
        app.add_middleware(
            ResponseBandwidthLimiterMiddleware,
            policy_evaluator=self._policy_evaluator,
            ip_manager=self._ip_manager,
            shutdown_coordinator=self._shutdown_coordinator,
            install_signal_handlers=install_signal_handlers,
        )
