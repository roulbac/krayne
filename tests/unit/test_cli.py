from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from krayne.api.types import ClusterDetails, ClusterInfo, HeadNodeInfo, WorkerGroupInfo
from krayne.cli.app import app

runner = CliRunner()

_INFO = ClusterInfo(
    name="test",
    namespace="default",
    status="ready",
    head_ip="10.0.0.1",
    dashboard_url="http://10.0.0.1:8265",
    client_url="ray://10.0.0.1:10001",
    notebook_url="http://10.0.0.1:8888",
    code_server_url=None,
    ssh_url="ssh://10.0.0.1:22",
    num_workers=2,
    created_at="2026-01-01T00:00:00Z",
)

_DETAILS = ClusterDetails(
    info=_INFO,
    head=HeadNodeInfo(cpus="2", memory="2Gi", gpus=0, image="rayproject/ray:2.41.0"),
    worker_groups=[
        WorkerGroupInfo(name="worker", replicas=2, cpus="2", memory="2Gi", gpus=0, gpu_type=None)
    ],
    ray_version="unknown",
    python_version="unknown",
)


class TestVersion:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output


class TestCreate:
    @patch("krayne.cli.app._get_cluster", return_value=_INFO)
    @patch("krayne.cli.app._create_cluster", return_value=_INFO)
    def test_create_basic(self, mock_create, mock_get):
        result = runner.invoke(app, ["create", "my-cluster"])
        assert result.exit_code == 0
        assert "Cluster Ready" in result.output
        mock_create.assert_called_once()

    @patch("krayne.cli.app._create_cluster", return_value=_INFO)
    def test_create_json_output(self, mock_create):
        result = runner.invoke(app, ["--output", "json", "create", "my-cluster"])
        assert result.exit_code == 0
        assert "test" in result.output

    @patch("krayne.cli.app._get_cluster")
    @patch("krayne.cli.app._create_cluster")
    def test_create_timeout_stops_polling(self, mock_create, mock_get):
        pending = ClusterInfo(
            name="test", namespace="default", status="pending",
            head_ip=None, dashboard_url=None, client_url=None,
            notebook_url=None, code_server_url=None, ssh_url=None, num_workers=0,
            created_at="2026-01-01T00:00:00Z",
        )
        mock_create.return_value = pending
        mock_get.return_value = pending
        result = runner.invoke(app, ["create", "my-cluster", "--timeout", "1"])
        assert result.exit_code == 0


class TestGet:
    @patch("krayne.cli.app._list_clusters", return_value=[_INFO])
    def test_get_clusters(self, mock_list):
        result = runner.invoke(app, ["get"])
        assert result.exit_code == 0
        assert "test" in result.output

    @patch("krayne.cli.app._list_clusters", return_value=[])
    def test_get_empty(self, mock_list):
        result = runner.invoke(app, ["get"])
        assert result.exit_code == 0


class TestDescribe:
    @patch("krayne.cli.app._describe_cluster", return_value=_DETAILS)
    def test_describe(self, mock_describe):
        result = runner.invoke(app, ["describe", "test"])
        assert result.exit_code == 0
        assert "test" in result.output


class TestScale:
    @patch("krayne.cli.app._scale_cluster", return_value=_INFO)
    def test_scale(self, mock_scale):
        result = runner.invoke(app, ["scale", "test", "--replicas", "4"])
        assert result.exit_code == 0


class TestDelete:
    @patch("krayne.cli.app._delete_cluster")
    def test_delete_with_force(self, mock_delete):
        result = runner.invoke(app, ["delete", "test", "--force"])
        assert result.exit_code == 0
        assert "deleted" in result.output
        mock_delete.assert_called_once()

    @patch("krayne.cli.app._delete_cluster")
    def test_delete_confirm_yes(self, mock_delete):
        result = runner.invoke(app, ["delete", "test"], input="y\n")
        assert result.exit_code == 0
        mock_delete.assert_called_once()

    @patch("krayne.cli.app._delete_cluster")
    def test_delete_confirm_no(self, mock_delete):
        result = runner.invoke(app, ["delete", "test"], input="n\n")
        assert result.exit_code != 0
        mock_delete.assert_not_called()


class TestInit:
    KUBECONFIG_YAML = (
        "apiVersion: v1\n"
        "kind: Config\n"
        "contexts:\n"
        "- name: my-context\n"
        "  context:\n"
        "    cluster: my-cluster\n"
        "    user: my-user\n"
        "current-context: my-context\n"
    )

    @patch("krayne.cli.app.save_krayne_settings")
    def test_init_headless(self, mock_save, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text(self.KUBECONFIG_YAML)
        result = runner.invoke(
            app, ["init", "--kubeconfig", str(kubeconfig), "--context", "my-context"]
        )
        assert result.exit_code == 0
        assert "Initialized" in result.output
        mock_save.assert_called_once()

    @patch("krayne.cli.app.save_krayne_settings")
    def test_init_interactive(self, mock_save, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text(self.KUBECONFIG_YAML)
        with patch("questionary.select") as mock_select, \
             patch("questionary.path") as mock_path:
            # First select call: pick "Custom path"
            mock_select.return_value.ask.return_value = "Custom path"
            mock_path.return_value.ask.return_value = str(kubeconfig)
            result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        mock_save.assert_called_once()

    def test_init_nonexistent_file(self):
        result = runner.invoke(
            app,
            ["init", "--kubeconfig", "/nonexistent/kubeconfig", "--context", "x"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Error" in result.output

    def test_init_invalid_context(self, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text(self.KUBECONFIG_YAML)
        result = runner.invoke(
            app,
            ["init", "--kubeconfig", str(kubeconfig), "--context", "nope"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Error" in result.output


class TestGlobalKubeconfig:
    @patch("krayne.cli.app._list_clusters", return_value=[_INFO])
    def test_kubeconfig_flag_passed(self, mock_list, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")
        result = runner.invoke(app, ["--kubeconfig", str(kubeconfig), "get"])
        assert result.exit_code == 0
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("kubeconfig") == str(kubeconfig)


class TestSandboxSetup:
    @patch("krayne.cli.app._setup_sandbox", return_value="/home/user/.krayne/sandbox-kubeconfig")
    def test_sandbox_setup(self, mock_setup):
        result = runner.invoke(app, ["sandbox", "setup"])
        assert result.exit_code == 0
        assert "Sandbox Ready" in result.output
        mock_setup.assert_called_once()


class TestSandboxTeardown:
    @patch("krayne.cli.app._teardown_sandbox")
    def test_sandbox_teardown(self, mock_teardown):
        result = runner.invoke(app, ["sandbox", "teardown"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()
        mock_teardown.assert_called_once()


class TestSandboxStatusCli:
    @patch("krayne.cli.app._sandbox_status")
    def test_sandbox_status(self, mock_status):
        from krayne.sandbox.manager import SandboxStatus

        mock_status.return_value = SandboxStatus(
            running=True,
            container_id="abc123",
            kubeconfig="/home/user/.krayne/sandbox-kubeconfig",
            k3s_version="rancher/k3s:v1.35.2-k3s1",
            created_at="2026-01-01T00:00:00Z",
        )
        result = runner.invoke(app, ["sandbox", "status"])
        assert result.exit_code == 0
        assert "Sandbox Status" in result.output


class TestTunOpen:
    @patch("krayne.tunnel.is_tunnel_active", return_value=False)
    @patch("krayne.tunnel.start_tunnels")
    @patch("krayne.cli.app._get_cluster_services", return_value=["dashboard", "client"])
    @patch("krayne.cli.app._get_cluster", return_value=_INFO)
    def test_tun_open_not_ready(self, mock_get, mock_services, mock_tunnels, mock_active):
        not_ready = ClusterInfo(
            name="test", namespace="default", status="pending",
            head_ip=None, dashboard_url=None, client_url=None,
            notebook_url=None, code_server_url=None, ssh_url=None, num_workers=0,
            created_at="2026-01-01T00:00:00Z",
        )
        mock_get.return_value = not_ready
        result = runner.invoke(app, ["tun-open", "test"])
        assert result.exit_code == 1
        assert "not ready" in result.output.lower()
        mock_tunnels.assert_not_called()

    @patch("krayne.tunnel.is_tunnel_active", return_value=False)
    @patch("krayne.tunnel.start_tunnels")
    @patch("krayne.cli.app._get_cluster_services", return_value=["dashboard", "client"])
    @patch("krayne.cli.app._get_cluster", return_value=_INFO)
    def test_tun_open_json_output(self, mock_get, mock_services, mock_tunnels, mock_active):
        from krayne.tunnel import TunnelInfo

        tunnels = [
            TunnelInfo(service="dashboard", remote_port=8265, local_port=12345, local_url="http://localhost:12345"),
        ]
        mock_tunnels.return_value = tunnels
        result = runner.invoke(app, ["--output", "json", "tun-open", "test"])
        assert result.exit_code == 0
        assert "dashboard" in result.output

    @patch("krayne.tunnel.load_tunnel_state")
    @patch("krayne.tunnel.is_tunnel_active", return_value=True)
    def test_tun_open_idempotent(self, mock_active, mock_load):
        from krayne.tunnel import TunnelInfo, TunnelState

        state = TunnelState(
            cluster_name="test", namespace="default",
            tunnels=[TunnelInfo(service="dashboard", remote_port=8265, local_port=12345, local_url="http://localhost:12345")],
            pids=[123],
        )
        mock_load.return_value = state
        result = runner.invoke(app, ["tun-open", "test"])
        assert result.exit_code == 0
        assert "already active" in result.output.lower()


class TestTunClose:
    @patch("krayne.tunnel.stop_tunnels", return_value=True)
    def test_tun_close_active(self, mock_stop):
        result = runner.invoke(app, ["tun-close", "test"])
        assert result.exit_code == 0
        assert "stopped" in result.output.lower()

    @patch("krayne.tunnel.stop_tunnels", return_value=False)
    def test_tun_close_noop(self, mock_stop):
        result = runner.invoke(app, ["tun-close", "test"])
        assert result.exit_code == 0
        assert "no active tunnel" in result.output.lower()
