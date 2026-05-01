from typing import Any, Iterable, Optional

from starlette.requests import Request
from starlette.routing import Match
from starlette.types import Scope


def _get_configured_handler_name(
    route: Any,
    endpoint: Any,
    path: str,
    configured_names: set[str],
) -> Optional[str]:
    endpoint_name = getattr(endpoint, "__name__", None)
    if endpoint_name in configured_names:
        return endpoint_name

    route_name = getattr(route, "name", None)
    if route_name in configured_names:
        return route_name

    route_path = getattr(route, "path", path).strip("/")
    if route_path in configured_names:
        return route_path

    if endpoint_name:
        for suffix in ["_response", "_endpoint"]:
            if endpoint_name.endswith(suffix):
                base_name = endpoint_name[:-len(suffix)]
                if base_name in configured_names:
                    return base_name

    return None


def _find_configured_handler_name(
    routes: Iterable[Any],
    scope: Scope,
    path: str,
    configured_names: set[str],
) -> Optional[str]:
    for route in routes:
        if not hasattr(route, "matches"):
            continue

        match, child_scope = route.matches(scope)
        if match != Match.FULL:
            continue

        endpoint = child_scope.get("endpoint", getattr(route, "endpoint", None))
        handler_name = _get_configured_handler_name(route, endpoint, path, configured_names)
        if handler_name is not None:
            return handler_name

        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            nested_scope = scope.copy()
            nested_scope.update(child_scope)
            handler_name = _find_configured_handler_name(nested_routes, nested_scope, path, configured_names)
            if handler_name is not None:
                return handler_name

    return None


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
