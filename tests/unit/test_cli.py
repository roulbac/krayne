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
