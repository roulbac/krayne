from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from threading import Thread

import pytest

from krayne import tunnel_state
from krayne.tunnel_state import (
    KubeConfigRef,
    ManagerInfo,
    ServiceState,
    ServiceStatus,
    TunnelInfo,
    TunnelState,
)


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    tunnel_dir = tmp_path / "tunnels"
    monkeypatch.setattr("krayne.tunnel_state.TUNNEL_DIR", tunnel_dir)
    return tunnel_dir


class TestRoundtrip:
    def test_save_load_roundtrip(self, isolated_state):
        state = TunnelState(
            cluster_name="c",
            namespace="ns",
            desired_tunnels=[
                TunnelInfo(service="dashboard", remote_port=8265,
                           local_port=12345, local_url="http://localhost:12345"),
            ],
            kube_config=KubeConfigRef(kubeconfig="/k", context="ctx"),
            manager=ManagerInfo(pid=42, start_time=1.0, heartbeat=2.0),
            status={"dashboard": ServiceStatus(
                state=ServiceState.OPEN, pod="head-xyz",
                restart_count=1, last_restart=3.0, last_error="x",
            )},
        )
        tunnel_state.save_state(state)

        loaded = tunnel_state.load_state("c", "ns")
        assert loaded is not None
        assert loaded.cluster_name == "c"
        assert loaded.namespace == "ns"
        assert loaded.kube_config.kubeconfig == "/k"
        assert loaded.kube_config.context == "ctx"
        assert loaded.manager is not None
        assert loaded.manager.pid == 42
        assert loaded.manager.start_time == 1.0
        assert loaded.manager.heartbeat == 2.0
        assert loaded.status["dashboard"].state == ServiceState.OPEN
        assert loaded.status["dashboard"].pod == "head-xyz"

    def test_load_returns_none_when_missing(self, isolated_state):
        assert tunnel_state.load_state("nonexistent", "default") is None

    def test_tunnels_alias(self, isolated_state):
        state = TunnelState(
            cluster_name="c", namespace="ns",
            desired_tunnels=[TunnelInfo(
                service="dashboard", remote_port=8265,
                local_port=1, local_url="http://localhost:1",
            )],
        )
        assert state.tunnels == state.desired_tunnels


class TestLegacyReap:
    def test_v1_file_pids_sigtermed_and_discarded(self, isolated_state):
        # Spawn a real sleeping process; assert it gets SIGTERMed.
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            path = tunnel_state.state_path("c", "ns")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "cluster_name": "c",
                "namespace": "ns",
                "tunnels": [{"service": "dashboard", "remote_port": 8265,
                             "local_port": 1, "local_url": "http://localhost:1"}],
                "pids": [proc.pid],
            }))

            assert tunnel_state.load_state("c", "ns") is None
            assert not path.exists()

            # Give the OS up to 2s to deliver the signal.
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pytest.fail("legacy PID was not SIGTERMed")
            assert proc.returncode != 0  # killed by signal
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_corrupt_file_discarded(self, isolated_state):
        path = tunnel_state.state_path("c", "ns")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not valid json")

        assert tunnel_state.load_state("c", "ns") is None
        assert not path.exists()

    def test_empty_file_returns_none(self, isolated_state):
        path = tunnel_state.state_path("c", "ns")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

        assert tunnel_state.load_state("c", "ns") is None


class TestUpdateAtomicity:
    def test_concurrent_updates_dont_lose(self, isolated_state):
        """Two threads each increment a counter under update(); both wins."""
        ITER = 50

        def bump():
            for _ in range(ITER):
                def _inc(s):
                    s.status["dashboard"] = ServiceStatus(
                        state=ServiceState.OPEN,
                        restart_count=s.status.get(
                            "dashboard", ServiceStatus()
                        ).restart_count + 1,
                    )
                    return s

                tunnel_state.update("c", "ns", _inc)

        threads = [Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final = tunnel_state.load_state("c", "ns")
        assert final is not None
        assert final.status["dashboard"].restart_count == ITER * 4


class TestManagerAlive:
    def test_none_is_not_alive(self):
        assert tunnel_state.manager_alive(None) is False

    def test_stale_heartbeat_is_not_alive(self):
        info = ManagerInfo(
            pid=os.getpid(),
            start_time=0.0,  # mismatched start_time guarantees False anyway
            heartbeat=time.time() - 60,  # 60s ago, stale
        )
        assert tunnel_state.manager_alive(info, heartbeat_max_age=10.0) is False

    def test_alive_when_our_pid_matches(self):
        import psutil
        proc = psutil.Process(os.getpid())
        info = ManagerInfo(
            pid=os.getpid(),
            start_time=proc.create_time(),
            heartbeat=time.time(),
        )
        assert tunnel_state.manager_alive(info, heartbeat_max_age=10.0) is True

    def test_fingerprint_mismatch_is_not_alive(self):
        info = ManagerInfo(
            pid=os.getpid(),
            start_time=0.0,
            heartbeat=time.time(),
        )
        assert tunnel_state.manager_alive(info) is False


class TestWaitUntilOpen:
    def test_returns_when_open(self, isolated_state):
        def _seed(s):
            s.status = {"dashboard": ServiceStatus(state=ServiceState.OPEN)}
            return s
        tunnel_state.update("c", "ns", _seed)

        result = tunnel_state.wait_until_open(
            "c", "ns", {"dashboard"}, timeout=1.0, interval=0.05,
        )
        assert result["dashboard"].state == ServiceState.OPEN

    def test_returns_when_failed(self, isolated_state):
        def _seed(s):
            s.status = {"dashboard": ServiceStatus(
                state=ServiceState.FAILED, last_error="boom",
            )}
            return s
        tunnel_state.update("c", "ns", _seed)

        result = tunnel_state.wait_until_open(
            "c", "ns", {"dashboard"}, timeout=1.0, interval=0.05,
        )
        assert result["dashboard"].state == ServiceState.FAILED

    def test_timeout(self, isolated_state):
        def _seed(s):
            s.status = {"dashboard": ServiceStatus(state=ServiceState.PENDING)}
            return s
        tunnel_state.update("c", "ns", _seed)

        with pytest.raises(TimeoutError):
            tunnel_state.wait_until_open(
                "c", "ns", {"dashboard"}, timeout=0.1, interval=0.02,
            )
