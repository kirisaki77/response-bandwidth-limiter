from typing import Dict, Any, List, Optional
from .middleware import BandwidthLimiterMiddleware

class FastAPIBandwidthLimiterMiddleware(BandwidthLimiterMiddleware):
    """FastAPI専用の帯域制限ミドルウェア"""
    
    def get_routes(self) -> List[Any]:
        """FastAPIのルート情報を取得"""
        return self.app.routes


class StarletteBandwidthLimiterMiddleware(BandwidthLimiterMiddleware):
    """Starlette専用の帯域制限ミドルウェア"""
    
    def get_routes(self) -> List[Any]:
        """Starletteのルート情報を取得"""
        return self.app.routes
    
    def get_handler_name(self, path: str) -> Optional[str]:
        """
        Starletteのルートからハンドラー名を取得
        Note: Starletteのルートは構造が異なる場合があるため、
        必要に応じてここをカスタマイズしてください
        """
        for route in self.get_routes():
            if hasattr(route, "path") and route.path == path:
                # Starletteのルート名をチェック
                route_name = getattr(route, "name", None) or getattr(route, "endpoint", None)
                if route_name and route_name in self.endpoint_bandwidth_limits:
                    return route_name
        return None
