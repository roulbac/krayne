from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from krayne import tunnel_state
from krayne.tunnel import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    SERVICE_PORTS,
    TunnelInfo,
    check_service_health,
    detect_services,
    is_tunnel_active,
    load_tunnel_state,
    local_port_for,
    start_tunnels,
    stop_tunnel_service,
    stop_tunnels,
    wait_for_tunnel_ready,
)
from krayne.tunnel_state import ServiceState, ServiceStatus


# --- Pure helpers (unchanged) -----------------------------------------------


class TestLocalPortFor:
    def test_deterministic(self):
        p1 = local_port_for("my-cluster", "default", "dashboard")
        p2 = local_port_for("my-cluster", "default", "dashboard")
        assert p1 == p2

    def test_in_range(self):
        port = local_port_for("my-cluster", "default", "dashboard")
        assert PORT_RANGE_START <= port < PORT_RANGE_END

    def test_different_clusters_different_ports(self):
        p1 = local_port_for("cluster-a", "default", "dashboard")
        p2 = local_port_for("cluster-b", "default", "dashboard")
        assert p1 != p2

    def test_different_services_different_ports(self):
        p1 = local_port_for("my-cluster", "default", "dashboard")
        p2 = local_port_for("my-cluster", "default", "notebook")
        assert p1 != p2

    def test_different_namespaces_different_ports(self):
        p1 = local_port_for("my-cluster", "ns-a", "dashboard")
        p2 = local_port_for("my-cluster", "ns-b", "dashboard")
        assert p1 != p2


class TestDetectServices:
    def test_all_services(self):
        obj = {
            "spec": {
                "headGroupSpec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "ray-head",
                                    "ports": [
                                        {"containerPort": 6379, "name": "gcs-server"},
                                        {"containerPort": 8265, "name": "dashboard"},
                                        {"containerPort": 10001, "name": "client"},
                                    ],
                                },
                            ]
                        }
                    },
                    "headService": {
                        "spec": {
                            "ports": [
                                {"name": "notebook", "port": 8888},
                                {"name": "ssh", "port": 22},
                                {"name": "code-server", "port": 8443},
                            ]
                        }
                    },
                }
            }
        }
        services = detect_services(obj)
        assert set(services) == {"dashboard", "client", "notebook", "code-server", "ssh"}

    def test_base_only(self):
        obj = {
            "spec": {
                "headGroupSpec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": "ray-head",
                                    "ports": [
                                        {"containerPort": 6379, "name": "gcs-server"},
                                        {"containerPort": 8265, "name": "dashboard"},
                                        {"containerPort": 10001, "name": "client"},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
        services = detect_services(obj)
        assert set(services) == {"dashboard", "client"}

    def test_empty_spec(self):
        services = detect_services({})
        assert services == []


class TestCheckServiceHealth:
    def test_pending_when_cluster_not_ready(self):
        health = check_service_health(
            cluster_status="creating",
            head_ip=None,
            declared_services=["dashboard", "client"],
            tunnel_map={},
        )
        assert health == {"dashboard": "pending", "client": "pending"}

    def test_no_targets_falls_back_to_available(self):
        health = check_service_health(
            cluster_status="ready",
            head_ip=None,
            declared_services=["dashboard"],
            tunnel_map={},
        )
        assert health == {"dashboard": "available"}

    def test_tunnel_target_probed(self):
        with patch("krayne.tunnel._tcp_probe", return_value=True) as probe:
            health = check_service_health(
                cluster_status="ready",
                head_ip="10.0.0.1",
                declared_services=["dashboard"],
                tunnel_map={"dashboard": "http://localhost:54321"},
            )
        assert health == {"dashboard": "available"}
        probe.assert_called_once_with("localhost", 54321, 0.5)

    def test_unreachable_when_probe_fails(self):
        with patch("krayne.tunnel._tcp_probe", return_value=False):
            health = check_service_health(
                cluster_status="ready",
                head_ip=None,
                declared_services=["dashboard"],
                tunnel_map={"dashboard": "http://localhost:54321"},
            )
        assert health == {"dashboard": "unreachable"}

    def test_head_ip_used_when_no_tunnel(self):
        with patch("krayne.tunnel._tcp_probe", return_value=True) as probe:
            check_service_health(
                cluster_status="ready",
                head_ip="10.0.0.1",
                declared_services=["dashboard"],
                tunnel_map={},
            )
        probe.assert_called_once_with("10.0.0.1", SERVICE_PORTS["dashboard"][0], 0.5)

    def test_mixed_results_per_service(self):
        results_by_target = {
            ("localhost", 54321): True,
            ("10.0.0.1", 8888): False,
        }

        def fake_probe(host, port, _timeout):
            return results_by_target.get((host, port), False)

        with patch("krayne.tunnel._tcp_probe", side_effect=fake_probe):
            health = check_service_health(
                cluster_status="ready",
                head_ip="10.0.0.1",
                declared_services=["dashboard", "notebook"],
                tunnel_map={"dashboard": "http://localhost:54321"},
            )
        assert health == {"dashboard": "available", "notebook": "unreachable"}


# --- Lifecycle (manager-based) ---------------------------------------------


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    """Redirect TUNNEL_DIR so writes don't touch the user's ~/.krayne."""
    tunnel_dir = tmp_path / "tunnels"
    monkeypatch.setattr("krayne.tunnel_state.TUNNEL_DIR", tunnel_dir)
    monkeypatch.setattr("krayne.tunnel.TUNNEL_DIR", tunnel_dir)
    return tunnel_dir


def _fake_manager_open(monkeypatch, services):
    """Patch out manager spawning + readiness wait, simulating a healthy
    manager that opened every requested service.

    Returns the mock used for ``_ensure_manager_running`` so tests can
    assert on call counts / args.
    """
    spawned: list[object] = []

    def fake_ensure(state):
        # Simulate the manager claiming its slot and marking services OPEN.
        def _claim(s):
            s.status = {
                svc: ServiceStatus(state=ServiceState.OPEN)
                for svc in services
            }
            from krayne.tunnel_state import ManagerInfo
            import time as _t
            s.manager = ManagerInfo(pid=99999, start_time=_t.time(), heartbeat=_t.time())
            return s

        tunnel_state.update(state.cluster_name, state.namespace, _claim)
        spawned.append(state)

    monkeypatch.setattr("krayne.tunnel._ensure_manager_running", fake_ensure)
    # Manager always reports alive in this fake.
    monkeypatch.setattr(
        "krayne.tunnel_state.manager_alive", lambda info, **_: info is not None,
    )
    return spawned


class TestStartTunnels:
    def test_writes_desired_state(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard", "notebook"])

        tunnels = start_tunnels(
            "my-cluster", "default", ["dashboard", "notebook"],
        )
        assert len(tunnels) == 2
        services = {t.service for t in tunnels}
        assert services == {"dashboard", "notebook"}

        state = load_tunnel_state("my-cluster", "default")
        assert state is not None
        assert state.cluster_name == "my-cluster"
        assert {t.service for t in state.desired_tunnels} == services

    def test_kubeconfig_persisted(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])

        start_tunnels(
            "c", "ns", ["dashboard"],
            kubeconfig="/my/kubeconfig", context="my-ctx",
        )
        state = load_tunnel_state("c", "ns")
        assert state is not None
        assert state.kube_config.kubeconfig == "/my/kubeconfig"
        assert state.kube_config.context == "my-ctx"

    def test_kubeconfig_and_context_from_settings(
        self, isolated_state, tmp_path, monkeypatch,
    ):
        """When neither is supplied, both load from ~/.krayne/config.yaml."""
        from krayne.config.settings import KrayneSettings, save_krayne_settings

        monkeypatch.setattr(
            "krayne.config.settings.PRISM_DIR", tmp_path / "krayne",
        )
        monkeypatch.setattr(
            "krayne.config.settings.PRISM_CONFIG_FILE",
            tmp_path / "krayne" / "config.yaml",
        )

        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text(
            "apiVersion: v1\n"
            "kind: Config\n"
            "contexts:\n"
            "- name: settings-ctx\n"
            "  context: {cluster: c, user: u}\n"
        )
        save_krayne_settings(
            KrayneSettings(kubeconfig=str(kubeconfig), kube_context="settings-ctx")
        )

        _fake_manager_open(monkeypatch, ["dashboard"])
        start_tunnels("c", "ns", ["dashboard"])

        state = load_tunnel_state("c", "ns")
        assert state is not None
        assert state.kube_config.kubeconfig == str(kubeconfig)
        assert state.kube_config.context == "settings-ctx"

    def test_tunnel_info_fields(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])

        tunnels = start_tunnels("c", "ns", ["dashboard"])

        t = tunnels[0]
        assert t.service == "dashboard"
        assert t.remote_port == 8265
        assert t.local_port == local_port_for("c", "ns", "dashboard")
        assert t.local_url == f"http://localhost:{t.local_port}"

    def test_unknown_service_skipped(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, [])
        tunnels = start_tunnels("c", "ns", ["nonexistent"])
        assert tunnels == []

    def test_wait_false_returns_without_polling(self, isolated_state, monkeypatch):
        """wait=False should write desired state + spawn but not block."""
        spawned = []
        monkeypatch.setattr(
            "krayne.tunnel._ensure_manager_running",
            lambda state: spawned.append(state),
        )

        tunnels = start_tunnels("c", "ns", ["dashboard"], wait=False)
        assert len(tunnels) == 1
        assert len(spawned) == 1

    def test_raises_when_service_marked_failed(self, isolated_state, monkeypatch):
        def fake_ensure(state):
            def _mark_failed(s):
                from krayne.tunnel_state import ManagerInfo
                import time as _t
                s.manager = ManagerInfo(pid=1, start_time=_t.time(), heartbeat=_t.time())
                s.status = {
                    "dashboard": ServiceStatus(
                        state=ServiceState.FAILED, last_error="bind: refused",
                    )
                }
                return s

            tunnel_state.update(state.cluster_name, state.namespace, _mark_failed)

        monkeypatch.setattr("krayne.tunnel._ensure_manager_running", fake_ensure)
        monkeypatch.setattr(
            "krayne.tunnel_state.manager_alive", lambda info, **_: info is not None,
        )

        with pytest.raises(Exception) as excinfo:
            start_tunnels("c", "ns", ["dashboard"])
        assert "dashboard" in str(excinfo.value)


class TestStopTunnels:
    def test_clears_desired_set(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])
        start_tunnels("c", "ns", ["dashboard"])

        # Pretend the manager exited cleanly when we asked it to.
        monkeypatch.setattr(
            "krayne.tunnel_state.manager_alive", lambda info, **_: False,
        )
        assert stop_tunnels("c", "ns") is True
        assert load_tunnel_state("c", "ns") is None

    def test_returns_false_when_no_state(self, isolated_state):
        assert stop_tunnels("nonexistent", "default") is False

    def test_stop_service_removes_single_entry(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard", "notebook"])
        start_tunnels("c", "ns", ["dashboard", "notebook"])

        assert stop_tunnel_service("c", "ns", "dashboard") is True
        state = load_tunnel_state("c", "ns")
        assert state is not None
        assert [t.service for t in state.desired_tunnels] == ["notebook"]

    def test_stop_service_returns_false_for_missing(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])
        start_tunnels("c", "ns", ["dashboard"])

        assert stop_tunnel_service("c", "ns", "nonexistent") is False


class TestIsTunnelActive:
    def test_no_state(self, isolated_state):
        assert is_tunnel_active("c", "ns") is False

    def test_alive_manager_active(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])
        start_tunnels("c", "ns", ["dashboard"])
        # Fake leaves the alive predicate in place; tunnel is "active".
        assert is_tunnel_active("c", "ns") is True

    def test_dead_manager_clears_slot(self, isolated_state, monkeypatch):
        _fake_manager_open(monkeypatch, ["dashboard"])
        start_tunnels("c", "ns", ["dashboard"])

        # Now flip the fake to report the manager as dead. is_tunnel_active
        # should clear the manager + status field for the next caller.
        monkeypatch.setattr(
            "krayne.tunnel_state.manager_alive", lambda info, **_: False,
        )
        assert is_tunnel_active("c", "ns") is False
        state = load_tunnel_state("c", "ns")
        assert state is not None
        assert state.manager is None


class TestEnsureManagerSpawn:
    """Asserts that Popen is invoked with the expected manager argv."""

    def test_subprocess_args(self, isolated_state, monkeypatch):
        # Alive iff state.manager is set. Initially None → spawn triggers.
        monkeypatch.setattr(
            "krayne.tunnel_state.manager_alive",
            lambda info, **_: info is not None,
        )

        # When Popen is called, simulate the manager claiming its slot and
        # marking dashboard OPEN so the subsequent wait returns immediately.
        def fake_popen(*_args, **_kwargs):
            from krayne.tunnel_state import ManagerInfo
            import time as _t

            def _claim(s):
                s.manager = ManagerInfo(
                    pid=1, start_time=_t.time(), heartbeat=_t.time(),
                )
                s.status = {"dashboard": ServiceStatus(state=ServiceState.OPEN)}
                return s

            tunnel_state.update("c", "ns", _claim)
            return MagicMock(pid=1)

        mock_popen = MagicMock(side_effect=fake_popen)
        monkeypatch.setattr("krayne.tunnel.subprocess.Popen", mock_popen)

        start_tunnels("c", "ns", ["dashboard"])

        assert mock_popen.call_count == 1
        argv = mock_popen.call_args[0][0]
        assert argv[1:] == ["-m", "krayne._manager", "c", "ns"]
        # Detached invocation.
        assert mock_popen.call_args.kwargs.get("start_new_session") is True


# --- TCP probe (unchanged) -------------------------------------------------


class TestWaitForTunnelReady:
    _tunnel = TunnelInfo(
        service="dashboard",
        remote_port=8265,
        local_port=54321,
        local_url="http://localhost:54321",
    )

    def test_returns_true_when_port_immediately_open(self):
        with patch("krayne.tunnel._tcp_probe", return_value=True) as probe:
            assert wait_for_tunnel_ready(self._tunnel, timeout=5.0) is True
        probe.assert_called_once()

    def test_returns_true_after_a_few_retries(self):
        with patch("krayne.tunnel._tcp_probe", side_effect=[False, False, True]):
            assert wait_for_tunnel_ready(self._tunnel, timeout=5.0, interval=0.0) is True

    def test_returns_false_on_timeout(self):
        with patch("krayne.tunnel._tcp_probe", return_value=False):
            assert wait_for_tunnel_ready(self._tunnel, timeout=0.05, interval=0.01) is False
