from unittest.mock import Mock

import pytest
from starlette.requests import Request

from response_bandwidth_limiter import Reject, ResponseBandwidthLimiter, Rule
from response_bandwidth_limiter.identity import get_client_identifier, get_client_ip, resolve_scope_identifiers


def request(host="192.0.2.1", headers=()):
    return Request({"type": "http", "client": (host, 1234), "headers": list(headers)})


@pytest.mark.parametrize("trusted, expected", [(False, "192.0.2.1"), (True, "2001:db8::1")])
def test_proxy_identity_obeys_trust_setting(trusted, expected):
    req = request(headers=[
        (b"x-forwarded-for", b"invalid, 2001:0db8::1, 203.0.113.1"),
        (b"x-real-ip", b"198.51.100.1"),
    ])
    assert get_client_ip(req, trusted) == expected
    assert get_client_identifier(req, trusted) == expected


def test_non_ip_client_only_identifies_default_scope():
    req = request("testclient")
    assert get_client_ip(req) is None
    assert get_client_identifier(req) == "testclient"
    rules = [Rule(1, "minute", Reject(), scope=name) for name in ("ip", "default")]
    assert resolve_scope_identifiers(req, rules, ResponseBandwidthLimiter()) == {
        "ip": "unknown", "default": "testclient",
    }


def test_custom_scope_is_resolved_once_per_request():
    limiter = ResponseBandwidthLimiter()
    resolver = Mock(return_value="user-42")
    limiter.register_scope_resolver("user", resolver)
    rule = Rule(1, "minute", Reject(), scope="user")
    req = request()
    assert resolve_scope_identifiers(req, [rule, rule], limiter) == {"user": "user-42"}
    resolver.assert_called_once_with(req)


@pytest.mark.parametrize("result", [None, "", "   ", RuntimeError("resolver failed")])
def test_custom_scope_falls_back_to_client_ip(result):
    limiter = ResponseBandwidthLimiter()
    resolver = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    limiter.register_scope_resolver("user", resolver)
    rule = Rule(1, "minute", Reject(), scope="user")
    req = request(headers=[(b"x-forwarded-for", b"198.51.100.1")])
    assert resolve_scope_identifiers(req, [rule], limiter) == {"user": "192.0.2.1"}


def test_missing_custom_scope_fails_explicitly():
    rule = Rule(1, "minute", Reject(), scope="user")
    with pytest.raises(ValueError, match="not registered"):
        resolve_scope_identifiers(request(), [rule], ResponseBandwidthLimiter())
