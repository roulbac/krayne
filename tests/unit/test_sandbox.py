"""Unit tests for prism.sandbox.manager (mocked subprocess calls)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from prism.errors import (
    DockerNotFoundError,
    SandboxAlreadyExistsError,
    SandboxError,
    SandboxNotFoundError,
)
from prism.sandbox.manager import (
    SANDBOX_CONTAINER_NAME,
    SandboxStatus,
    sandbox_status,
    setup_sandbox,
    teardown_sandbox,
)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Redirect all settings and kubeconfig paths to tmp."""
    import prism.config.settings as settings_mod
    import prism.sandbox.manager as mgr_mod

    monkeypatch.setattr(settings_mod, "PRISM_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "PRISM_CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(mgr_mod, "SANDBOX_KUBECONFIG", tmp_path / "sandbox-kubeconfig")


# ---------------------------------------------------------------------------
# setup_sandbox
# ---------------------------------------------------------------------------


class TestSetupSandbox:
    def _mock_run_side_effect(self, tmp_path):
        """Build a side_effect function that handles setup_sandbox's subprocess calls."""
        kubeconfig_content = "apiVersion: v1\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n"

        def side_effect(cmd, **kwargs):
            cmd_str = " ".join(cmd)
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""

            if "docker info" in cmd_str:
                pass
            elif "docker inspect" in cmd_str and SANDBOX_CONTAINER_NAME in cmd_str:
                # First call (existence check) should fail, subsequent ones succeed
                result.returncode = 1
                raise subprocess.CalledProcessError(1, cmd, "", "not found")
            elif "docker run -d" in cmd_str:
                result.stdout = "container_id_123"
            elif "docker exec" in cmd_str and "kubectl" in cmd_str:
                result.stdout = "Ready"
            elif "docker exec" in cmd_str and "cat" in cmd_str:
                result.stdout = kubeconfig_content
            elif "alpine/helm" in cmd_str:
                pass
            elif "kubectl" in cmd_str and "crd" in cmd_str:
                pass
            elif "kubectl" in cmd_str and "deployment" in cmd_str:
                result.stdout = "1"

            return result

        return side_effect

    @patch("prism.sandbox.manager.subprocess.run")
    @patch("prism.sandbox.manager._container_exists", return_value=False)
    @patch("prism.sandbox.manager._wait_for_k3s")
    @patch("prism.sandbox.manager._wait_for_crds")
    @patch("prism.sandbox.manager._wait_for_deployment")
    def test_setup_success(
        self, mock_deploy, mock_crds, mock_k3s, mock_exists, mock_run, tmp_path
    ):
        kubeconfig_content = "apiVersion: v1\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n"

        def side_effect(cmd, **kwargs):
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stdout = kubeconfig_content
            result.stderr = ""
            return result

        mock_run.side_effect = side_effect

        path = setup_sandbox()
        assert path.endswith("sandbox-kubeconfig")
        assert Path(path).exists()

    @patch("prism.sandbox.manager._run")
    def test_docker_not_found(self, mock_run):
        mock_run.side_effect = SandboxError("Command not found: docker")
        with pytest.raises(DockerNotFoundError):
            setup_sandbox()

    @patch("prism.sandbox.manager._run")
    @patch("prism.sandbox.manager._container_exists", return_value=True)
    def test_already_exists(self, mock_exists, mock_run):
        # _run for docker info should succeed
        mock_run.return_value = MagicMock(returncode=0)
        with pytest.raises(SandboxAlreadyExistsError):
            setup_sandbox()


# ---------------------------------------------------------------------------
# teardown_sandbox
# ---------------------------------------------------------------------------


class TestTeardownSandbox:
    @patch("prism.sandbox.manager._run")
    @patch("prism.sandbox.manager._container_exists", return_value=True)
    def test_teardown_success(self, mock_exists, mock_run, tmp_path):
        # Create a sandbox kubeconfig to verify cleanup
        import prism.sandbox.manager as mgr_mod
        mgr_mod.SANDBOX_KUBECONFIG.write_text("kubeconfig")

        teardown_sandbox()
        mock_run.assert_called_once()
        assert not mgr_mod.SANDBOX_KUBECONFIG.exists()

    @patch("prism.sandbox.manager._container_exists", return_value=False)
    def test_teardown_not_found(self, mock_exists):
        with pytest.raises(SandboxNotFoundError):
            teardown_sandbox()


# ---------------------------------------------------------------------------
# sandbox_status
# ---------------------------------------------------------------------------


class TestSandboxStatus:
    @patch("prism.sandbox.manager.subprocess.run")
    def test_status_running(self, mock_run, tmp_path):
        import json
        import prism.sandbox.manager as mgr_mod

        mgr_mod.SANDBOX_KUBECONFIG.write_text("kubeconfig")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "Id": "abc123def456",
                "State": {"Running": True},
                "Created": "2026-01-01T00:00:00Z",
                "Config": {"Image": "rancher/k3s:v1.35.2-k3s1"},
            }]),
        )

        status = sandbox_status()
        assert status.running is True
        assert status.container_id == "abc123def456"
        assert status.k3s_version == "rancher/k3s:v1.35.2-k3s1"

    @patch("prism.sandbox.manager.subprocess.run")
    def test_status_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        status = sandbox_status()
        assert status.running is False
        assert status.container_id is None
