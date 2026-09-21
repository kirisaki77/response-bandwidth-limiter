import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import httpx
import pytest


class LiveServer:
    def __init__(self, process, directory):
        self.process = process
        self.directory = directory
        self.url = None

    def logs(self):
        return (self.directory / "server.log").read_text(encoding="utf-8", errors="replace")

    def wait_ready(self):
        deadline = time.monotonic() + 20
        with httpx.Client(timeout=0.5, trust_env=False) as client:
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    pytest.fail("Server exited during startup:\n" + self.logs())
                port_file = self.directory / "port"
                if port_file.exists():
                    port = port_file.read_text(encoding="ascii").strip()
                    if port:
                        self.url = "http://127.0.0.1:" + port
                        try:
                            if client.get(self.url + "/health").status_code == 200:
                                return
                        except httpx.TransportError:
                            pass
                time.sleep(0.05)
        pytest.fail("Server readiness timed out:\n" + self.logs())

    def client(self):
        return httpx.Client(base_url=self.url, timeout=15, trust_env=False)

    def stop(self):
        if self.process.poll() is None:
            if os.name == "nt":
                self.process.terminate()
            else:
                self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.fixture
def live_server(tmp_path):
    servers = []

    def start(*, redis_prefix=None):
        directory = tmp_path / str(len(servers))
        directory.mkdir()
        command = [sys.executable, "-I", str(Path(__file__).with_name("server.py")), str(directory)]
        if redis_prefix:
            command += ["--redis-prefix", redis_prefix]
        with (directory / "server.log").open("wb") as log:
            process = subprocess.Popen(
                command, cwd=directory, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        server = LiveServer(process, directory)
        servers.append(server)
        server.wait_ready()
        return server

    yield start
    for server in reversed(servers):
        server.stop()
        # Visible in pytest failure reports and CI logs; no pipe can fill up.
        print(server.logs())
