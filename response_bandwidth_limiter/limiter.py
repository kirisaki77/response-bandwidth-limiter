import functools
from typing import Dict, Callable, List, Union
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
        self.routes: Dict[str, int] = {}
        self.policies: Dict[str, List[Rule]] = {}
        self.key_func = key_func  # slowapi互換のため、キー関数を受け入れる
        
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
            self.routes[endpoint_name] = rate
            
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
            self.policies[endpoint_name] = list(rules)

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
        app.state.response_bandwidth_limits = self.routes
        app.state.response_bandwidth_policies = self.policies
        app.state.response_bandwidth_limiter = self
        app.add_middleware(ResponseBandwidthLimiterMiddleware)
        app.add_exception_handler(ResponseBandwidthLimitExceeded, _response_bandwidth_limit_exceeded_handler)
