"""Starts the mock vendor-risk API in a background thread for the tests.

The deterministic tests need the real HTTP tool, including its 503 path, but
they should not depend on the developer remembering to run run_local.py first.
"""
from __future__ import annotations

import os
import socket
import threading
import time

import requests
import uvicorn

from mock_api.app import app

_server: uvicorn.Server | None = None
_thread: threading.Thread | None = None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start() -> str:
    """Start the API and point the client at it. Returns the base URL."""
    global _server, _thread
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    _server = uvicorn.Server(config)
    _thread = threading.Thread(target=_server.run, daemon=True)
    _thread.start()

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            if requests.get(f"{base_url}/health", timeout=0.5).ok:
                break
        except requests.RequestException:
            time.sleep(0.1)
    else:
        raise RuntimeError("The mock vendor-risk API did not start in time.")

    os.environ["VENDOR_RISK_BASE_URL"] = base_url
    return base_url


def stop() -> None:
    if _server is not None:
        _server.should_exit = True
    if _thread is not None:
        _thread.join(timeout=5)
