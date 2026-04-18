from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import StreamingResponse
from starlette.routing import Match
import asyncio
from typing import Dict, Any, List, Optional, AsyncIterator
from .errors import ResponseBandwidthLimitExceeded
from .decorator import endpoint_bandwidth_limits

class ResponseBandwidthLimiterMiddleware(BaseHTTPMiddleware):
    chunk_size = 8192

    def __init__(self, app: Any):
        """
        帯域制限ミドルウェア
        
        Args:
            app: FastAPIまたはStarletteアプリ
        """
        super().__init__(app)
        self.endpoint_bandwidth_limits = {}
        
    def get_routes(self) -> List[Any]:
        """アプリケーションからルート情報を取得"""
        return getattr(self.app, "routes", [])

    def _get_limit_name(self, route: Any, endpoint: Any, path: str, combined_limits: Dict[str, int]) -> Optional[str]:
        endpoint_name = getattr(endpoint, "__name__", None)
        if endpoint_name in combined_limits:
            return endpoint_name

        route_name = getattr(route, "name", None)
        if route_name in combined_limits:
            return route_name

        route_path = getattr(route, "path", path).strip("/")
        if route_path in combined_limits:
            return route_path

        if endpoint_name:
            for suffix in ["_response", "_endpoint"]:
                if endpoint_name.endswith(suffix):
                    base_name = endpoint_name[:-len(suffix)]
                    if base_name in combined_limits:
                        return base_name

        return None

    def _find_handler_name(self, routes: List[Any], scope: Dict[str, Any], path: str, combined_limits: Dict[str, int]) -> Optional[str]:
        for route in routes:
            if not hasattr(route, "matches"):
                continue

            match, child_scope = route.matches(scope)
            if match != Match.FULL:
                continue

            endpoint = child_scope.get("endpoint", getattr(route, "endpoint", None))
            handler_name = self._get_limit_name(route, endpoint, path, combined_limits)
            if handler_name is not None:
                return handler_name

            nested_routes = getattr(route, "routes", None)
            if nested_routes:
                nested_scope = scope.copy()
                nested_scope.update(child_scope)
                handler_name = self._find_handler_name(nested_routes, nested_scope, path, combined_limits)
                if handler_name is not None:
                    return handler_name

        return None

    async def _iterate_in_chunks(self, body: bytes):
        for index in range(0, len(body), self.chunk_size):
            yield body[index:index + self.chunk_size]

    async def _yield_limited_chunks(self, chunk: bytes, max_rate: int) -> AsyncIterator[bytes]:
        effective_chunk_size = max(1, min(self.chunk_size, max_rate))
        for index in range(0, len(chunk), effective_chunk_size):
            part = chunk[index:index + effective_chunk_size]
            if not part:
                continue
            await asyncio.sleep(len(part) / max_rate)
            yield part

    def _build_streaming_response(self, response: Any, iterator: Any) -> StreamingResponse:
        streaming_response = StreamingResponse(
            iterator,
            status_code=response.status_code,
            media_type=response.media_type,
            background=response.background,
        )
        streaming_response.raw_headers = list(response.raw_headers)
        return streaming_response
        
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
        combined_limits.update(endpoint_bandwidth_limits)  # decorator.pyからの制限を追加
        app_state = getattr(app, "state", None)
        
        # アプリの状態からbandwidth_limitsまたはbandwidth_limiterを探す
        if app_state:
            if hasattr(app_state, "response_bandwidth_limits"):
                combined_limits.update(app_state.response_bandwidth_limits)
            elif hasattr(app_state, "response_bandwidth_limiter") and hasattr(app_state.response_bandwidth_limiter, "routes"):
                combined_limits.update(app_state.response_bandwidth_limiter.routes)
        
        # ルートを探索
        routes = getattr(app, "routes", [])
        return self._find_handler_name(routes, request.scope, path, combined_limits)

    async def dispatch(self, request: Request, call_next):
        """リクエストに対して帯域制限を適用"""
        # リクエストからアプリを取得
        app = request.scope.get("app", self.app)
        
        # 帯域制限を取得
        combined_limits = self.endpoint_bandwidth_limits.copy()
        combined_limits.update(endpoint_bandwidth_limits)  # decorator.pyからの制限を追加
        app_state = getattr(app, "state", None)
        
        # アプリの状態からbandwidth_limitsまたはbandwidth_limiterを探す
        if app_state:
            if hasattr(app_state, "response_bandwidth_limits"):
                combined_limits.update(app_state.response_bandwidth_limits)
            elif hasattr(app_state, "response_bandwidth_limiter") and hasattr(app_state.response_bandwidth_limiter, "routes"):
                combined_limits.update(app_state.response_bandwidth_limiter.routes)
        
        path = request.scope["path"]
        handler_name = self.get_handler_name(request, path)
        max_rate = combined_limits.get(handler_name, None)

        if max_rate is None:
            return await call_next(request)
            
        response = await call_next(request)

        async def limited_iterator(iterator):
            async for chunk in iterator:
                async for limited_chunk in self._yield_limited_chunks(chunk, max_rate):
                    yield limited_chunk

        # レスポンス本文を追加で連結せず、常にストリーミングで制限する
        if hasattr(response, "body_iterator"):
            return self._build_streaming_response(response, limited_iterator(response.body_iterator))

        if hasattr(response, "body") and response.body is not None:
            return self._build_streaming_response(response, limited_iterator(self._iterate_in_chunks(response.body)))

        if hasattr(response, "streaming"):
            response.streaming = limited_iterator(response.streaming)

        return response
