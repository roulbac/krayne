from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from krayne.errors import (
    DockerNotFoundError,
    SandboxAlreadyExistsError,
    SandboxError,
    SandboxNotFoundError,
)
from krayne.sandbox.manager import (
    SANDBOX_CONTAINER_NAME,
    SETUP_STEPS,
    STEP_DOCKER,
    STEP_K3S_CONTAINER,
    SandboxStatus,
    sandbox_status,
    setup_sandbox,
    teardown_sandbox,
)


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    """Redirect all settings and kubeconfig paths to tmp."""
    import krayne.config.settings as settings_mod
    import krayne.sandbox.manager as mgr_mod

    monkeypatch.setattr(settings_mod, "PRISM_DIR", tmp_path)
    monkeypatch.setattr(settings_mod, "PRISM_CONFIG_FILE", tmp_path / "config.yaml")
    monkeypatch.setattr(mgr_mod, "SANDBOX_KUBECONFIG", tmp_path / "sandbox-kubeconfig")


class TestSetupSandbox:
    @staticmethod
    def _make_side_effect():
        kubeconfig_content = "apiVersion: v1\nclusters:\n- cluster:\n    server: https://127.0.0.1:6443\n"

        def side_effect(cmd, **kwargs):
            result = MagicMock(spec=subprocess.CompletedProcess)
            result.returncode = 0
            result.stderr = ""
            if "docker" in cmd and "info" in cmd:
                result.stdout = "8 8589934592"
            else:
                result.stdout = kubeconfig_content
            return result

        return side_effect

    @patch("krayne.sandbox.manager.subprocess.run")
    @patch("krayne.sandbox.manager._container_exists", return_value=False)
    @patch("krayne.sandbox.manager._wait_for_k3s")
    @patch("krayne.sandbox.manager._wait_for_crds")
    @patch("krayne.sandbox.manager._wait_for_deployment")
    def test_setup_success(
        self, mock_deploy, mock_crds, mock_k3s, mock_exists, mock_run, tmp_path
    ):
        mock_run.side_effect = self._make_side_effect()

        path = setup_sandbox()
        assert path.endswith("sandbox-kubeconfig")
        assert Path(path).exists()

    @patch("krayne.sandbox.manager.subprocess.run")
    @patch("krayne.sandbox.manager._container_exists", return_value=False)
    @patch("krayne.sandbox.manager._wait_for_k3s")
    @patch("krayne.sandbox.manager._wait_for_crds")
    @patch("krayne.sandbox.manager._wait_for_deployment")
    def test_setup_progress_callback(
        self, mock_deploy, mock_crds, mock_k3s, mock_exists, mock_run, tmp_path
    ):
        mock_run.side_effect = self._make_side_effect()
        callback = MagicMock()

        setup_sandbox(on_progress=callback)

        # Every step should have been reported as "done"
        done_calls = [
            c for c in callback.call_args_list if c[0][1] == "done"
        ]
        done_steps = {c[0][0] for c in done_calls}
        assert done_steps == set(SETUP_STEPS)

        # Docker should be reported as in_progress then done
        docker_calls = [
            c[0][1] for c in callback.call_args_list if c[0][0] == STEP_DOCKER
        ]
        assert docker_calls[0] == "in_progress"
        assert docker_calls[-1] == "done"

    @patch("krayne.sandbox.manager._run")
    def test_docker_not_found(self, mock_run):
        mock_run.side_effect = SandboxError("Command not found: docker")
        with pytest.raises(DockerNotFoundError):
            setup_sandbox()

    @patch("krayne.sandbox.manager._run")
    @patch("krayne.sandbox.manager._container_exists", return_value=True)
    def test_already_exists(self, mock_exists, mock_run):
        # _run for docker info should succeed with enough resources
        mock_run.return_value = MagicMock(returncode=0, stdout="8 8589934592")
        with pytest.raises(SandboxAlreadyExistsError):
            setup_sandbox()


class TestTeardownSandbox:
    @patch("krayne.sandbox.manager._run")
    @patch("krayne.sandbox.manager._container_exists", return_value=True)
    def test_teardown_success(self, mock_exists, mock_run, tmp_path):
        # Create a sandbox kubeconfig to verify cleanup
        import krayne.sandbox.manager as mgr_mod
        mgr_mod.SANDBOX_KUBECONFIG.write_text("kubeconfig")

        teardown_sandbox()
        mock_run.assert_called_once()
        assert not mgr_mod.SANDBOX_KUBECONFIG.exists()

    @patch("krayne.sandbox.manager._container_exists", return_value=False)
    def test_teardown_not_found(self, mock_exists):
        with pytest.raises(SandboxNotFoundError):
            teardown_sandbox()


class TestSandboxStatus:
    @patch("krayne.sandbox.manager.subprocess.run")
    def test_status_running(self, mock_run, tmp_path):
        import json
        import krayne.sandbox.manager as mgr_mod

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

    @patch("krayne.sandbox.manager.subprocess.run")
    def test_status_not_running(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="")
        status = sandbox_status()
        assert status.running is False
        assert status.container_id is None
