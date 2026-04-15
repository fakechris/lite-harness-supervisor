"""Tests for daemon idle shutdown and lifecycle state."""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from supervisor.daemon.server import DaemonServer, DEFAULT_IDLE_SHUTDOWN_SEC


def test_idle_shutdown_default():
    """Default idle shutdown is 10 minutes."""
    assert DEFAULT_IDLE_SHUTDOWN_SEC == 600


def test_daemon_does_not_idle_exit_with_active_runs():
    """Idle shutdown skipped when runs are active."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {"run_1": MagicMock()}
    server._lock = __import__("threading").Lock()
    server.idle_shutdown_sec = 1
    server._started_at = time.time() - 100
    server._last_run_finished_at = 0
    server._last_client_contact_at = time.time() - 100
    server._shutdown = __import__("threading").Event()
    server.sock_path = "/tmp/test.sock"

    server._check_idle_shutdown()
    assert not server._shutdown.is_set()


def test_daemon_idle_exits_when_no_runs_and_timeout():
    """Daemon sets shutdown event when idle long enough with zero runs."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {}
    server._lock = __import__("threading").Lock()
    server.idle_shutdown_sec = 5
    server._started_at = time.time() - 100
    server._last_run_finished_at = time.time() - 10
    server._last_client_contact_at = time.time() - 10
    server._shutdown = __import__("threading").Event()
    server.sock_path = "/tmp/test.sock"

    with patch("supervisor.daemon.server.update_daemon"):
        server._check_idle_shutdown()
    assert server._shutdown.is_set()


def test_daemon_idle_not_triggered_within_timeout():
    """Daemon stays alive if idle time hasn't reached threshold."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {}
    server._lock = __import__("threading").Lock()
    server.idle_shutdown_sec = 600
    server._started_at = time.time()
    server._last_run_finished_at = time.time()
    server._last_client_contact_at = time.time()
    server._shutdown = __import__("threading").Event()
    server.sock_path = "/tmp/test.sock"

    server._check_idle_shutdown()
    assert not server._shutdown.is_set()


def test_daemon_idle_disabled_with_zero_timeout():
    """Idle shutdown disabled when idle_shutdown_sec=0."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {}
    server._lock = __import__("threading").Lock()
    server.idle_shutdown_sec = 0
    server._started_at = time.time() - 9999
    server._last_run_finished_at = 0
    server._last_client_contact_at = time.time() - 9999
    server._shutdown = __import__("threading").Event()

    server._check_idle_shutdown()
    assert not server._shutdown.is_set()


def test_daemon_metadata_includes_state():
    """Daemon metadata includes state and idle_shutdown_sec."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {"r1": MagicMock()}
    server.sock_path = "/tmp/test.sock"
    server.idle_shutdown_sec = 600

    meta = server._daemon_metadata()
    assert meta["state"] == "active"
    assert meta["idle_shutdown_sec"] == 600

    server._runs = {}
    meta = server._daemon_metadata()
    assert meta["state"] == "idle"


def test_reap_updates_last_run_finished():
    """Reaping finished runs updates _last_run_finished_at."""
    server = DaemonServer.__new__(DaemonServer)
    server._runs = {}
    server._lock = __import__("threading").Lock()
    server._last_run_finished_at = 0
    server.sock_path = "/tmp/test.sock"

    # Nothing to reap
    server._reap_finished()
    assert server._last_run_finished_at == 0  # unchanged
