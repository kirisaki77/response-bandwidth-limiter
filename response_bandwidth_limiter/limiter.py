import functools
from typing import Callable, Dict, List, Mapping, Union
from fastapi import Request, FastAPI
from starlette.applications import Starlette
from .errors import ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler
from .middleware import ResponseBandwidthLimiterMiddleware
from .models import Rule

class ResponseBandwidthLimiter:
    """
    レスポンス帯域幅制限の装飾子を提供するクラス
    
    Example:
        ```
        from response_bandwidth_limiter import ResponseBandwidthLimiter, _response_bandwidth_limit_exceeded_handler
        from response_bandwidth_limiter.errors import ResponseBandwidthLimitExceeded
        from fastapi import FastAPI, Request
        
        limiter = ResponseBandwidthLimiter()
        app = FastAPI()
        app.state.response_bandwidth_limiter = limiter
        app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)
        
        @app.get("/download")
        @limiter.limit(1024)  # 1024 bytes/sec
        async def download_file(request: Request):
            return FileResponse(...)
        ```
    """
    def __init__(self, key_func: Callable = None):
        self._route_limits: Dict[str, int] = {}
        self._route_policies: Dict[str, List[Rule]] = {}
        self.key_func = key_func  # slowapi互換のため、キー関数を受け入れる

    @property
    def routes(self) -> Mapping[str, int]:
        return dict(self._route_limits)

    @property
    def policies(self) -> Mapping[str, List[Rule]]:
        return {name: list(rules) for name, rules in self._route_policies.items()}

    @property
    def configured_names(self) -> set[str]:
        return set(self._route_limits) | set(self._route_policies)

    def get_limit(self, endpoint_name: str) -> int | None:
        return self._route_limits.get(endpoint_name)

    def get_rules(self, endpoint_name: str) -> List[Rule]:
        return list(self._route_policies.get(endpoint_name, []))

    def update_route(self, endpoint_name: str, rate: int) -> None:
        if not isinstance(endpoint_name, str) or not endpoint_name:
            raise ValueError("endpoint_name は空でない文字列である必要があります。")
        if not isinstance(rate, int):
            raise TypeError("帯域制限値は整数である必要があります。")
        if rate <= 0:
            raise ValueError("帯域制限値は1以上である必要があります。")
        self._route_limits[endpoint_name] = rate

    def remove_route(self, endpoint_name: str) -> None:
        self._route_limits.pop(endpoint_name, None)

    def update_policy(self, endpoint_name: str, rules: List[Rule]) -> None:
        if not isinstance(endpoint_name, str) or not endpoint_name:
            raise ValueError("endpoint_name は空でない文字列である必要があります。")
        if not isinstance(rules, list):
            raise TypeError("rules は Rule の配列である必要があります。")
        if not rules:
            raise ValueError("rules は1件以上指定する必要があります。")
        if not all(isinstance(rule, Rule) for rule in rules):
            raise TypeError("rules には Rule のみ指定できます。")
        self._route_policies[endpoint_name] = list(rules)

    def remove_policy(self, endpoint_name: str) -> None:
        self._route_policies.pop(endpoint_name, None)
        
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
        if not isinstance(rate, int):
            raise TypeError("帯域制限値は整数である必要があります。例: @limiter.limit(1024)")
        if rate <= 0:
            raise ValueError("帯域制限値は1以上である必要があります。無効化する場合は設定を削除してください。")
            
        def decorator(func):
            # 関数名を保存
            endpoint_name = func.__name__
            self.update_route(endpoint_name, rate)
            
            @functools.wraps(func)
            async def wrapper(request: Request, *args, **kwargs):
                # requestパラメータを必ず含める必要あり
                return await func(request, *args, **kwargs)
                
            # FastAPIで使用するためにエンドポイント名を保存
            wrapper.endpoint_name = endpoint_name
            return wrapper
            
        return decorator

    def limit_rules(self, rules: List[Rule]) -> Callable:
        """
        request count ベースのポリシーを設定する装飾子

        Args:
            rules: Rule の配列

        Returns:
            装飾子関数
        """
        if not isinstance(rules, list):
            raise TypeError("rules は Rule の配列である必要があります。")
        if not rules:
            raise ValueError("rules は1件以上指定する必要があります。")
        if not all(isinstance(rule, Rule) for rule in rules):
            raise TypeError("rules には Rule のみ指定できます。")

        def decorator(func):
            endpoint_name = func.__name__
            self.update_policy(endpoint_name, rules)

            @functools.wraps(func)
            async def wrapper(request: Request, *args, **kwargs):
                return await func(request, *args, **kwargs)

            wrapper.endpoint_name = endpoint_name
            return wrapper

        return decorator
        
    def init_app(self, app: Union[FastAPI, Starlette]) -> None:
        """
        アプリケーションにリミッターを登録する
        
        Args:
            app: FastAPIまたはStarletteアプリケーション
        """
        app.state.response_bandwidth_limiter = self
        app.add_middleware(ResponseBandwidthLimiterMiddleware)
        app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)
