from __future__ import annotations

from unittest.mock import MagicMock, patch

from prism.tunnel import (
    PORT_RANGE_END,
    PORT_RANGE_START,
    SERVICE_PORTS,
    detect_services,
    local_port_for,
    start_tunnels,
)


class TestLocalPortFor:
    def test_deterministic(self):
        """Same inputs always produce the same port."""
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
                                        {"containerPort": 8888, "name": "notebook"},
                                        {"containerPort": 22, "name": "ssh"},
                                    ],
                                },
                                {
                                    "name": "vscode",
                                    "ports": [
                                        {"containerPort": 8080, "name": "vscode"},
                                    ],
                                },
                            ]
                        }
                    }
                }
            }
        }
        services = detect_services(obj)
        assert set(services) == {"dashboard", "client", "notebook", "vscode", "ssh"}

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


class TestStartTunnels:
    @patch("prism.tunnel.subprocess.Popen")
    def test_launches_kubectl_processes(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        tunnels, processes = start_tunnels(
            "my-cluster", "default", ["dashboard", "notebook"]
        )

        assert len(tunnels) == 2
        assert len(processes) == 2
        assert mock_popen.call_count == 2

        # Check first call is for dashboard
        first_call_args = mock_popen.call_args_list[0][0][0]
        assert "kubectl" in first_call_args
        assert "port-forward" in first_call_args
        assert "svc/my-cluster-head-svc" in first_call_args
        assert "-n" in first_call_args
        assert "default" in first_call_args

    @patch("prism.tunnel.subprocess.Popen")
    def test_kubeconfig_passed(self, mock_popen):
        mock_popen.return_value = MagicMock()

        start_tunnels(
            "c", "ns", ["dashboard"], kubeconfig="/my/kubeconfig"
        )

        call_args = mock_popen.call_args_list[0][0][0]
        assert "--kubeconfig" in call_args
        assert "/my/kubeconfig" in call_args

    @patch("prism.tunnel.subprocess.Popen")
    def test_tunnel_info_fields(self, mock_popen):
        mock_popen.return_value = MagicMock()

        tunnels, _ = start_tunnels("c", "ns", ["dashboard"])

        t = tunnels[0]
        assert t.service == "dashboard"
        assert t.remote_port == 8265
        assert t.local_port == local_port_for("c", "ns", "dashboard")
        assert t.local_url == f"http://localhost:{t.local_port}"

    @patch("prism.tunnel.subprocess.Popen")
    def test_unknown_service_skipped(self, mock_popen):
        mock_popen.return_value = MagicMock()

        tunnels, processes = start_tunnels("c", "ns", ["nonexistent"])

        assert len(tunnels) == 0
        assert len(processes) == 0
