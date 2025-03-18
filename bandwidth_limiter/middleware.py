from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
import asyncio
from typing import Dict, Callable, Any, List, Optional
from .errors import BandwidthLimitExceeded

class BandwidthLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, limits: Optional[Dict[str, int]] = None):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
            limits: エンドポイント名と制限速度(bytes/sec)の辞書
        """
        super().__init__(app)
        self.endpoint_bandwidth_limits = limits or {}
        
    def get_routes(self) -> List[Any]:
        """アプリケーションからルート情報を取得"""
        return getattr(self.app, "routes", [])
        
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
        
        # 帯域制限を取得
        combined_limits = self.endpoint_bandwidth_limits.copy()
        app_state = getattr(app, "state", None)
        
        # アプリの状態からbandwidth_limitsまたはbandwidth_limiterを探す
        if app_state:
            if hasattr(app_state, "bandwidth_limits"):
                combined_limits.update(app_state.bandwidth_limits)
            elif hasattr(app_state, "bandwidth_limiter") and hasattr(app_state.bandwidth_limiter, "routes"):
                combined_limits.update(app_state.bandwidth_limiter.routes)
        
        # ルートを探索
        routes = getattr(app, "routes", [])
        for route in routes:
            if hasattr(route, "path") and route.path == path:
                if hasattr(route, "name") and route.name in combined_limits:
                    return route.name
                # FastAPIのエンドポイント関数名を確認
                if hasattr(route, "endpoint") and hasattr(route.endpoint, "__name__"):
                    endpoint_name = route.endpoint.__name__
                    if endpoint_name in combined_limits:
                        return endpoint_name
        return None

    async def dispatch(self, request: Request, call_next):
        """リクエストに対して帯域制限を適用"""
        # リクエストからアプリを取得
        app = request.scope.get("app", self.app)
        
        # 帯域制限を取得
        combined_limits = self.endpoint_bandwidth_limits.copy()
        app_state = getattr(app, "state", None)
        
        # アプリの状態からbandwidth_limitsまたはbandwidth_limiterを探す
        if app_state:
            if hasattr(app_state, "bandwidth_limits"):
                combined_limits.update(app_state.bandwidth_limits)
            elif hasattr(app_state, "bandwidth_limiter") and hasattr(app_state.bandwidth_limiter, "routes"):
                combined_limits.update(app_state.bandwidth_limiter.routes)
        
        path = request.scope["path"]
        handler_name = self.get_handler_name(request, path)
        max_rate = combined_limits.get(handler_name, None)

        if max_rate is None:
            return await call_next(request)
            
        response = await call_next(request)

        async def limited_iterator(iterator):
            async for chunk in iterator:
                yield chunk
                if len(chunk) > 0:  # 0バイト分割を避ける
                    await asyncio.sleep(len(chunk) / max_rate)

        # レスポンスの属性をチェックして安全に処理
        if hasattr(response, "body") and response.body:
            response.body = b"".join([chunk async for chunk in limited_iterator([response.body])])
        elif hasattr(response, "body_iterator"):
            # StreamingResponseの場合
            original_iterator = response.body_iterator
            response.body_iterator = limited_iterator(original_iterator)
        elif hasattr(response, "streaming"):
            response.streaming = limited_iterator(response.streaming)

        return response
