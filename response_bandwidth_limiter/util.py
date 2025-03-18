from fastapi import Request
from typing import Callable, Any

def get_endpoint_name(request: Request) -> str:
    """リクエストからエンドポイント名を取得する"""
    return request.scope.get("endpoint", request.scope.get("path", ""))

def get_route_path(request: Request) -> str:
    """リクエストからルートパスを取得する"""
    return request.scope.get("path", "")
