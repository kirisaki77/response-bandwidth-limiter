import functools
from typing import Dict, Any, Callable, Optional, Union
from fastapi import Request, FastAPI
from starlette.applications import Starlette
from .errors import BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler
from .middleware import BandwidthLimiterMiddleware

class BandwidthLimiter:
    """
    帯域幅制限の装飾子を提供するクラス
    
    Example:
        ```
        from bandwidth_limiter import BandwidthLimiter, _bandwidth_limit_exceeded_handler
        from bandwidth_limiter.errors import BandwidthLimitExceeded
        from fastapi import FastAPI, Request
        
        limiter = BandwidthLimiter()
        app = FastAPI()
        app.state.bandwidth_limiter = limiter
        app.add_exception_handler(BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler)
        
        @app.get("/download")
        @limiter.limit(1024)  # 1024 bytes/sec
        async def download_file(request: Request):
            return FileResponse(...)
        ```
    """
    def __init__(self, key_func: Callable = None):
        self.routes: Dict[str, int] = {}
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
        
    def init_app(self, app: Union[FastAPI, Starlette]) -> None:
        """
        アプリケーションにリミッターを登録する
        
        Args:
            app: FastAPIまたはStarletteアプリケーション
        """
        app.state.bandwidth_limits = self.routes
        app.add_middleware(BandwidthLimiterMiddleware)
        app.add_exception_handler(BandwidthLimitExceeded, _bandwidth_limit_exceeded_handler)
