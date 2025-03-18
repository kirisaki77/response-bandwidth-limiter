from typing import Dict, Any, List, Optional
from .middleware import ResponseBandwidthLimiterMiddleware
from starlette.requests import Request

class FastAPIResponseBandwidthLimiterMiddleware(ResponseBandwidthLimiterMiddleware):
    """FastAPI専用の帯域制限ミドルウェア"""
    
    def get_routes(self) -> List[Any]:
        """FastAPIのルート情報を取得"""
        return getattr(self.app, "routes", [])


class StarletteResponseBandwidthLimiterMiddleware(ResponseBandwidthLimiterMiddleware):
    """Starlette専用の帯域制限ミドルウェア"""
    
    def get_routes(self, request: Optional[Request] = None) -> List[Any]:
        """
        Starletteのルート情報を取得
        
        Args:
            request: オプションのリクエストオブジェクト（指定されていれば、スコープからappを取得）
            
        Returns:
            ルート情報のリスト
        """
        # リクエストが提供された場合、スコープからアプリを取得
        if request and hasattr(request, "scope") and "app" in request.scope:
            app = request.scope["app"]
            if hasattr(app, "routes"):
                return app.routes
                
        # アプリがミドルウェアスタックの中にある場合のための対策
        if hasattr(self.app, "app") and hasattr(self.app.app, "routes"):
            return self.app.app.routes
            
        # 通常のアプリケーションの場合
        if hasattr(self.app, "routes"):
            return self.app.routes
            
        # 何も見つからない場合は空のリストを返す
        return []
    
    def get_handler_name(self, request: Request, path: str) -> Optional[str]:
        """
        Starletteのルートからハンドラー名を取得
        
        Args:
            request: リクエストオブジェクト
            path: リクエストパス
            
        Returns:
            エンドポイント名（存在する場合）
        """
        # リクエストを渡して、適切なアプリとルートを取得
        for route in self.get_routes(request):
            if hasattr(route, "path") and route.path == path:
                # Starletteのルート名をチェック
                route_name = getattr(route, "name", None) or getattr(route, "endpoint", None)
                if callable(route_name):
                    # エンドポイント関数の場合、__name__を取得
                    route_name = getattr(route_name, "__name__", None)
                
                if route_name and route_name in self.endpoint_bandwidth_limits:
                    return route_name
        return None
