from starlette.requests import Request

def get_endpoint_name(request: Request) -> str:
    """リクエストからエンドポイント名を取得する"""
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return request.scope.get("path", "")
    if isinstance(endpoint, str):
        return endpoint
    return getattr(endpoint, "__name__", str(endpoint))

def get_route_path(request: Request) -> str:
    """リクエストからルートパスを取得する"""
    return request.scope.get("path", "")
