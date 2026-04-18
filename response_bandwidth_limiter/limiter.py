import threading
from typing import Callable, Dict, List, Mapping, Optional

from starlette.applications import Starlette

from .middleware import ResponseBandwidthLimiterMiddleware
from .models import Rule
from .shutdown import ShutdownCoordinator, ShutdownMode

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
    def __init__(self, key_func: Optional[Callable] = None, trusted_proxy_headers: bool = False):
        self._lock = threading.RLock()
        self._route_limits: Dict[str, int] = {}
        self._route_policies: Dict[str, List[Rule]] = {}
        self._shutdown_coordinator = ShutdownCoordinator()
        self.key_func = key_func  # slowapi互換のため、キー関数を受け入れる
        self.trusted_proxy_headers = trusted_proxy_headers

    def _validate_endpoint_name(self, endpoint_name: str) -> None:
        if not isinstance(endpoint_name, str) or not endpoint_name:
            raise ValueError("endpoint_name は空でない文字列である必要があります。")

    def _validate_rate(self, rate: int, *, decorator_context: bool = False) -> None:
        if not isinstance(rate, int):
            if decorator_context:
                raise TypeError("帯域制限値は整数である必要があります。例: @limiter.limit(1024)")
            raise TypeError("帯域制限値は整数である必要があります。")
        if rate <= 0:
            if decorator_context:
                raise ValueError("帯域制限値は1以上である必要があります。無効化する場合は設定を削除してください。")
            raise ValueError("帯域制限値は1以上である必要があります。")

    def _validate_rules(self, rules: List[Rule]) -> None:
        if not isinstance(rules, list):
            raise TypeError("rules は Rule の配列である必要があります。")
        if not rules:
            raise ValueError("rules は1件以上指定する必要があります。")
        if not all(isinstance(rule, Rule) for rule in rules):
            raise TypeError("rules には Rule のみ指定できます。")

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

    def remove_policy(self, endpoint_name: str) -> None:
        with self._lock:
            self._route_policies.pop(endpoint_name, None)

    def begin_shutdown(self, mode: ShutdownMode) -> None:
        self._shutdown_coordinator.begin_shutdown(mode)

    async def shutdown(self, mode: ShutdownMode, timeout: float | None = None) -> bool:
        self.begin_shutdown(mode)
        return await self._shutdown_coordinator.wait_until_drained(timeout=timeout)
        
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
        app.state.response_bandwidth_limiter = self
        app.add_middleware(
            ResponseBandwidthLimiterMiddleware,
            shutdown_coordinator=self._shutdown_coordinator,
            install_signal_handlers=install_signal_handlers,
        )
