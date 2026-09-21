import os
import signal
import time
import uuid

import httpx
import pytest

pytestmark = pytest.mark.e2e


def wait_for_idle(client):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        state = client.get("/health")
        assert state.status_code == 200
        if state.json()["in_flight"] == 0:
            return
        time.sleep(0.05)
    pytest.fail("Disconnected/aborted response remained in flight")


@pytest.mark.parametrize("path", ["/download", "/stream", "/file"])
def test_real_http_bandwidth_and_payload(live_server, path):
    server = live_server()
    with server.client() as client:
        start = time.monotonic()
        with client.stream("GET", path) as response:
            assert response.status_code == 200
            parts = []
            first_arrival = None
            for part in response.iter_raw():
                if first_arrival is None:
                    first_arrival = time.monotonic() - start
                parts.append(part)
            elapsed = time.monotonic() - start
            assert b"".join(parts) == b"x" * 3072
            # Lower bounds detect missing throttling without flaky tight upper bounds.
            assert first_arrival is not None and first_arrival >= 0.8
            assert elapsed >= (0.8 if path == "/file" else 2.7)
            if path == "/download":
                assert response.headers["x-e2e"] == "preserved"
        wait_for_idle(client)


def test_policy_rejection_and_delay_over_http(live_server):
    with live_server().client() as client:
        assert client.get("/limited").status_code == 200
        rejected = client.get("/limited")
        assert rejected.status_code == 429
        assert int(rejected.headers["retry-after"]) > 0
        assert rejected.json()["error"] == "Rate Limit Exceeded"
        assert client.get("/delayed").status_code == 200
        start = time.monotonic()
        assert client.get("/delayed").status_code == 200
        assert time.monotonic() - start >= 0.25


def test_client_disconnect_releases_response(live_server):
    with live_server().client() as client:
        with client.stream("GET", "/endless") as response:
            assert response.status_code == 200
            chunks = response.iter_raw()
            assert next(chunks)
            assert client.get("/health").json()["in_flight"] == 1
        wait_for_idle(client)


@pytest.mark.parametrize("mode", ["drain", "abort"])
def test_shutdown_during_http_stream(live_server, mode):
    with live_server().client() as client:
        with client.stream("GET", "/stream") as response:
            assert response.status_code == 200
            chunks = response.iter_raw()
            received = next(chunks)
            assert client.post("/shutdown/" + mode).status_code == 200
            assert client.get("/download").status_code == 503
            if mode == "abort":
                with pytest.raises(httpx.RemoteProtocolError):
                    for chunk in chunks:
                        received += chunk
                assert len(received) < 3072
            else:
                received += b"".join(chunks)
                assert received == b"x" * 3072
        wait_for_idle(client)


@pytest.mark.skipif(os.name == "nt", reason="POSIX SIGINT delivery is tested on Linux CI")
def test_sigint_drains_response_and_closes_storage(live_server):
    server = live_server()
    with server.client() as client:
        with client.stream("GET", "/stream") as response:
            assert response.status_code == 200
            chunks = response.iter_raw()
            received = next(chunks)
            server.process.send_signal(signal.SIGINT)
            received += b"".join(chunks)
            assert received == b"x" * 3072
    server.process.wait(timeout=15)
    assert server.process.returncode in (0, -signal.SIGINT, 128 + signal.SIGINT), server.logs()
    assert (server.directory / "closed").read_text(encoding="utf-8") == "closed"


@pytest.mark.skipif(not os.getenv("REDIS_URL"), reason="REDIS_URL is not set")
def test_redis_shares_policy_and_ip_controls_between_processes(live_server):
    from redis import Redis

    prefix = "rbl-e2e-" + uuid.uuid4().hex
    admin = Redis.from_url(os.environ["REDIS_URL"])
    try:
        admin.ping()  # A configured but unavailable Redis must fail, never skip.
        first = live_server(redis_prefix=prefix)
        second = live_server(redis_prefix=prefix)
        with first.client() as a, second.client() as b:
            assert a.get("/health").json()["pid"] != b.get("/health").json()["pid"]
            assert a.get("/limited").status_code == 200
            rejected = b.get("/limited")
            assert rejected.status_code == 429
            assert int(rejected.headers["retry-after"]) > 0
            assert a.post("/block").status_code == 200
            assert b.get("/limited").status_code == 403
    finally:
        # Only remove keys belonging to this test; never flush a shared database.
        for key in admin.scan_iter(match=prefix + ":*"):
            admin.delete(key)
        admin.close()
