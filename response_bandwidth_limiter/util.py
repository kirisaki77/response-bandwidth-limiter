from typing import Any, Iterable, Optional

from starlette.requests import Request
from starlette.routing import Match
from starlette.types import Scope


def _append_configured_name(candidates: list[str], name: str | None, configured_names: set[str]) -> None:
    if name and name in configured_names and name not in candidates:
        candidates.append(name)


def _get_configured_handler_names(
    route: Any,
    endpoint: Any,
    path: str,
    configured_names: set[str],
) -> list[str]:
    candidates: list[str] = []

    endpoint_name = getattr(endpoint, "__name__", None)
    _append_configured_name(candidates, endpoint_name, configured_names)

    route_name = getattr(route, "name", None)
    _append_configured_name(candidates, route_name, configured_names)

    route_path = getattr(route, "path", path).strip("/")
    _append_configured_name(candidates, route_path, configured_names)

    if endpoint_name:
        for suffix in ["_response", "_endpoint"]:
            if endpoint_name.endswith(suffix):
                base_name = endpoint_name[:-len(suffix)]
                _append_configured_name(candidates, base_name, configured_names)

    return candidates


def _get_configured_handler_name(
    route: Any,
    endpoint: Any,
    path: str,
    configured_names: set[str],
) -> Optional[str]:
    handler_names = _get_configured_handler_names(route, endpoint, path, configured_names)
    return handler_names[0] if handler_names else None


def _find_configured_handler_names(
    routes: Iterable[Any],
    scope: Scope,
    path: str,
    configured_names: set[str],
) -> list[str]:
    for route in routes:
        if not hasattr(route, "matches"):
            continue

        match, child_scope = route.matches(scope)
        if match != Match.FULL:
            continue

        endpoint = child_scope.get("endpoint", getattr(route, "endpoint", None))
        handler_names = _get_configured_handler_names(route, endpoint, path, configured_names)
        if handler_names:
            return handler_names

        nested_routes = getattr(route, "routes", None)
        if nested_routes:
            nested_scope = scope.copy()
            nested_scope.update(child_scope)
            handler_names = _find_configured_handler_names(nested_routes, nested_scope, path, configured_names)
            if handler_names:
                return handler_names

    return []


def _find_configured_handler_name(
    routes: Iterable[Any],
    scope: Scope,
    path: str,
    configured_names: set[str],
) -> Optional[str]:
    handler_names = _find_configured_handler_names(routes, scope, path, configured_names)
    return handler_names[0] if handler_names else None


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
