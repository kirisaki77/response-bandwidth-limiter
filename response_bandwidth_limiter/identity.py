"""Resolve request identities for IP controls and policy scopes."""

import logging
from ipaddress import ip_address
from typing import Any, Optional

from starlette.requests import Request

from .models import Rule


logger = logging.getLogger(__name__)


def extract_valid_ip(raw_value: Optional[str]) -> Optional[str]:
    if raw_value is None:
        return None

    for candidate in raw_value.split(","):
        normalized = candidate.strip()
        if not normalized:
            continue
        try:
            normalized = str(ip_address(normalized))
        except ValueError:
            continue
        return normalized

    return None


def get_client_identifier(
    request: Request,
    trust_proxy_headers: bool = False,
) -> str:
    if trust_proxy_headers:
        forwarded_ip = extract_valid_ip(request.headers.get("x-forwarded-for"))
        if forwarded_ip is not None:
            return forwarded_ip

        real_ip = extract_valid_ip(request.headers.get("x-real-ip"))
        if real_ip is not None:
            return real_ip

    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return extract_valid_ip(str(client.host)) or client.host

    scope_client = request.scope.get("client")
    if scope_client:
        return extract_valid_ip(str(scope_client[0])) or str(scope_client[0])

    return "unknown"


def get_client_ip(request: Request, trust_proxy_headers: bool = False) -> str | None:
    if trust_proxy_headers:
        forwarded_ip = extract_valid_ip(request.headers.get("x-forwarded-for"))
        if forwarded_ip is not None:
            return forwarded_ip

        real_ip = extract_valid_ip(request.headers.get("x-real-ip"))
        if real_ip is not None:
            return real_ip

    client = getattr(request, "client", None)
    if client and getattr(client, "host", None):
        return extract_valid_ip(str(client.host))

    scope_client = request.scope.get("client")
    if scope_client:
        return extract_valid_ip(str(scope_client[0]))

    return None


def resolve_scope_identifiers(request: Request, rules: list[Rule], limiter: Any) -> dict[str, str]:
    scope_identifiers: dict[str, str] = {}
    trust_proxy_headers = getattr(limiter, "trusted_proxy_headers", False)

    for rule in rules:
        scope_name = rule.scope
        if scope_name in scope_identifiers:
            continue

        if scope_name == "ip":
            scope_identifiers[scope_name] = get_client_ip(request, trust_proxy_headers) or "unknown"
            continue

        if scope_name == "default":
            scope_identifiers[scope_name] = get_client_identifier(request, trust_proxy_headers)
            continue

        resolver = getattr(limiter, "_get_scope_resolver", None)
        if not callable(resolver):
            raise ValueError(f"Cannot resolve a resolver getter for scope {scope_name!r}.")

        scope_resolver = resolver(scope_name)
        if scope_resolver is None:
            raise ValueError(f"scope {scope_name!r} is not registered.")

        try:
            resolved = scope_resolver(request)
        except Exception:
            logger.warning(
                "Scope resolver %r raised an exception. Falling back to the real client IP.",
                scope_name,
                exc_info=True,
            )
            scope_identifiers[scope_name] = get_client_ip(request, trust_proxy_headers) or "unknown"
            continue

        str_value = str(resolved) if resolved is not None else ""
        if not str_value.strip():
            logger.warning(
                "Scope resolver %r returned an empty value. Falling back to the real client IP.",
                scope_name,
            )
            scope_identifiers[scope_name] = get_client_ip(request, trust_proxy_headers) or "unknown"
        else:
            scope_identifiers[scope_name] = str_value

    return scope_identifiers
