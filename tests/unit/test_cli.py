"""Unit tests for the Prism CLI using Typer's CliRunner."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from prism.api.types import ClusterDetails, ClusterInfo, HeadNodeInfo, WorkerGroupInfo
from prism.cli.app import app

runner = CliRunner()

_INFO = ClusterInfo(
    name="test",
    namespace="default",
    status="ready",
    head_ip="10.0.0.1",
    dashboard_url="http://10.0.0.1:8265",
    notebook_url=None,
    vscode_url=None,
    num_workers=2,
    created_at="2026-01-01T00:00:00Z",
)

_DETAILS = ClusterDetails(
    info=_INFO,
    head=HeadNodeInfo(cpus=15, memory="48Gi", gpus=0, image="rayproject/ray:2.41.0"),
    worker_groups=[
        WorkerGroupInfo(name="worker", replicas=2, cpus=15, memory="48Gi", gpus=0, gpu_type=None)
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
    @patch("prism.cli.app._create_cluster", return_value=_INFO)
    def test_create_basic(self, mock_create):
        result = runner.invoke(app, ["create", "my-cluster"])
        assert result.exit_code == 0
        assert "my-cluster" in result.output or "Cluster Created" in result.output
        mock_create.assert_called_once()

    @patch("prism.cli.app._create_cluster", return_value=_INFO)
    def test_create_json_output(self, mock_create):
        result = runner.invoke(app, ["--output", "json", "create", "my-cluster"])
        assert result.exit_code == 0
        assert "test" in result.output


class TestGet:
    @patch("prism.cli.app._list_clusters", return_value=[_INFO])
    def test_get_clusters(self, mock_list):
        result = runner.invoke(app, ["get"])
        assert result.exit_code == 0
        assert "test" in result.output

    @patch("prism.cli.app._list_clusters", return_value=[])
    def test_get_empty(self, mock_list):
        result = runner.invoke(app, ["get"])
        assert result.exit_code == 0


class TestDescribe:
    @patch("prism.cli.app._describe_cluster", return_value=_DETAILS)
    def test_describe(self, mock_describe):
        result = runner.invoke(app, ["describe", "test"])
        assert result.exit_code == 0
        assert "test" in result.output


class TestScale:
    @patch("prism.cli.app._scale_cluster", return_value=_INFO)
    def test_scale(self, mock_scale):
        result = runner.invoke(app, ["scale", "test", "--replicas", "4"])
        assert result.exit_code == 0


class TestDelete:
    @patch("prism.cli.app._delete_cluster")
    def test_delete_with_force(self, mock_delete):
        result = runner.invoke(app, ["delete", "test", "--force"])
        assert result.exit_code == 0
        assert "deleted" in result.output
        mock_delete.assert_called_once()

    @patch("prism.cli.app._delete_cluster")
    def test_delete_confirm_yes(self, mock_delete):
        result = runner.invoke(app, ["delete", "test"], input="y\n")
        assert result.exit_code == 0
        mock_delete.assert_called_once()

    @patch("prism.cli.app._delete_cluster")
    def test_delete_confirm_no(self, mock_delete):
        result = runner.invoke(app, ["delete", "test"], input="n\n")
        assert result.exit_code != 0
        mock_delete.assert_not_called()


class TestInit:
    @patch("prism.cli.app.save_prism_settings")
    def test_init_saves_config(self, mock_save, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")
        result = runner.invoke(app, ["init", str(kubeconfig)])
        assert result.exit_code == 0
        assert "Initialized" in result.output
        mock_save.assert_called_once()

    def test_init_nonexistent_file(self):
        result = runner.invoke(app, ["init", "/nonexistent/kubeconfig"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "Error" in result.output


class TestGlobalKubeconfig:
    @patch("prism.cli.app._list_clusters", return_value=[_INFO])
    def test_kubeconfig_flag_passed(self, mock_list, tmp_path):
        kubeconfig = tmp_path / "kubeconfig"
        kubeconfig.write_text("apiVersion: v1")
        result = runner.invoke(app, ["--kubeconfig", str(kubeconfig), "get"])
        assert result.exit_code == 0
        mock_list.assert_called_once()
        _, kwargs = mock_list.call_args
        assert kwargs.get("kubeconfig") == str(kubeconfig)


class TestSandboxSetup:
    @patch("prism.cli.app._setup_sandbox", return_value="/home/user/.prism/sandbox-kubeconfig")
    def test_sandbox_setup(self, mock_setup):
        result = runner.invoke(app, ["sandbox", "setup"])
        assert result.exit_code == 0
        assert "Sandbox Ready" in result.output
        mock_setup.assert_called_once()


class TestSandboxTeardown:
    @patch("prism.cli.app._teardown_sandbox")
    def test_sandbox_teardown(self, mock_teardown):
        result = runner.invoke(app, ["sandbox", "teardown"])
        assert result.exit_code == 0
        assert "removed" in result.output.lower()
        mock_teardown.assert_called_once()


class TestSandboxStatusCli:
    @patch("prism.cli.app._sandbox_status")
    def test_sandbox_status(self, mock_status):
        from prism.sandbox.manager import SandboxStatus

        mock_status.return_value = SandboxStatus(
            running=True,
            container_id="abc123",
            kubeconfig="/home/user/.prism/sandbox-kubeconfig",
            k3s_version="rancher/k3s:v1.35.2-k3s1",
            created_at="2026-01-01T00:00:00Z",
        )
        result = runner.invoke(app, ["sandbox", "status"])
        assert result.exit_code == 0
        assert "Sandbox Status" in result.output
