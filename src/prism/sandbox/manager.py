"""Sandbox lifecycle: set up and tear down a local k3s cluster with KubeRay."""

from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from prism.config.settings import (
    PRISM_DIR,
    clear_prism_settings,
    load_prism_settings,
    save_prism_settings,
    PrismSettings,
)
from prism.errors import (
    DockerNotFoundError,
    SandboxAlreadyExistsError,
    SandboxError,
    SandboxNotFoundError,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SANDBOX_CONTAINER_NAME = "prism-sandbox"
K3S_IMAGE = "rancher/k3s:v1.35.2-k3s1"
HELM_IMAGE = "alpine/helm"
KUBERAY_HELM_REPO = "https://ray-project.github.io/kuberay-helm"
SANDBOX_KUBECONFIG = PRISM_DIR / "sandbox-kubeconfig"


@dataclass(frozen=True)
class SandboxStatus:
    """Current state of the sandbox environment."""

    running: bool
    container_id: str | None = None
    kubeconfig: str | None = None
    k3s_version: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess, raising ``SandboxError`` on failure."""
    try:
        return subprocess.run(
            cmd, check=check, capture_output=True, text=True, **kwargs
        )
    except subprocess.CalledProcessError as exc:
        raise SandboxError(
            f"Command failed: {' '.join(cmd)}\n{exc.stderr}"
        ) from exc
    except FileNotFoundError as exc:
        raise SandboxError(f"Command not found: {cmd[0]}") from exc


def _container_exists() -> bool:
    """Return True if the sandbox container exists (running or stopped)."""
    result = subprocess.run(
        ["docker", "inspect", SANDBOX_CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _wait_for_k3s(timeout: int = 120) -> None:
    """Poll until the k3s node is ready inside the container."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker", "exec", SANDBOX_CONTAINER_NAME,
                "kubectl", "get", "nodes",
                "-o", "jsonpath={.items[0].status.conditions[-1].type}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and "Ready" in result.stdout:
            return
        time.sleep(3)
    raise SandboxError(f"K3S node not ready within {timeout}s")


def _wait_for_crds(kubeconfig: str, timeout: int = 120) -> None:
    """Wait until the RayCluster CRD is registered."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["kubectl", "--kubeconfig", kubeconfig, "get", "crd", "rayclusters.ray.io"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        time.sleep(3)
    raise SandboxError("RayCluster CRD not registered within timeout")


def _wait_for_deployment(
    name: str, kubeconfig: str, namespace: str = "default", timeout: int = 180
) -> None:
    """Wait until a deployment has at least one available replica."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "kubectl", "--kubeconfig", kubeconfig,
                "get", "deployment", name,
                "-n", namespace,
                "-o", "jsonpath={.status.availableReplicas}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "0", "null"):
            return
        time.sleep(5)
    raise SandboxError(f"Deployment {name} not available within {timeout}s")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def setup_sandbox() -> str:
    """Create a local k3s container with KubeRay and return the kubeconfig path.

    Steps:
        1. Verify Docker is available.
        2. Start a k3s container with port 6443 mapped.
        3. Wait for k3s to become ready.
        4. Extract the kubeconfig.
        5. Install KubeRay via Helm.
        6. Wait for the CRD and operator deployment.
        7. Save the kubeconfig as the active Prism config.
    """
    # 1. Check Docker
    try:
        _run(["docker", "info"])
    except SandboxError:
        raise DockerNotFoundError()

    # 2. Check for existing sandbox
    if _container_exists():
        raise SandboxAlreadyExistsError()

    # 3. Start k3s
    _run([
        "docker", "run", "-d",
        "--name", SANDBOX_CONTAINER_NAME,
        "--privileged",
        "-p", "6443:6443",
        "-e", "K3S_KUBECONFIG_MODE=644",
        K3S_IMAGE,
        "server", "--disable=traefik",
    ])

    try:
        # 4. Wait for k3s
        _wait_for_k3s()

        # 5. Extract kubeconfig
        result = _run([
            "docker", "exec", SANDBOX_CONTAINER_NAME,
            "cat", "/etc/rancher/k3s/k3s.yaml",
        ])
        raw_kubeconfig = result.stdout

        # Write host-accessible kubeconfig (127.0.0.1:6443 works via port mapping)
        PRISM_DIR.mkdir(parents=True, exist_ok=True)
        SANDBOX_KUBECONFIG.write_text(raw_kubeconfig)

        # Write internal kubeconfig for helm (shares container network)
        internal_kubeconfig = tempfile.NamedTemporaryFile(
            prefix="prism-sandbox-internal-", suffix=".yaml", delete=False
        )
        internal_kubeconfig.write(raw_kubeconfig.encode())
        internal_kubeconfig.close()
        internal_path = internal_kubeconfig.name

        # 6. Install KubeRay via Helm
        try:
            _run([
                "docker", "run", "--rm",
                "--network", f"container:{SANDBOX_CONTAINER_NAME}",
                "-v", f"{internal_path}:/root/.kube/config:ro",
                HELM_IMAGE,
                "install", "kuberay-operator", "kuberay-operator",
                "--repo", KUBERAY_HELM_REPO,
                "--namespace", "default",
            ])
        finally:
            Path(internal_path).unlink(missing_ok=True)

        # 7. Wait for CRD + operator
        kubeconfig_path = str(SANDBOX_KUBECONFIG)
        _wait_for_crds(kubeconfig_path)
        _wait_for_deployment("kuberay-operator", kubeconfig_path)

        # 8. Save as active config
        save_prism_settings(PrismSettings(kubeconfig=kubeconfig_path))

        return kubeconfig_path

    except Exception:
        # Clean up the container on any setup failure
        subprocess.run(
            ["docker", "rm", "-f", SANDBOX_CONTAINER_NAME],
            capture_output=True,
            text=True,
        )
        raise


def teardown_sandbox() -> None:
    """Stop and remove the sandbox container and clean up configuration."""
    if not _container_exists():
        raise SandboxNotFoundError()

    _run(["docker", "rm", "-f", SANDBOX_CONTAINER_NAME])

    # Remove sandbox kubeconfig
    if SANDBOX_KUBECONFIG.exists():
        SANDBOX_KUBECONFIG.unlink()

    # Clear prism settings if they point to the sandbox
    settings = load_prism_settings()
    if settings.kubeconfig == str(SANDBOX_KUBECONFIG):
        clear_prism_settings()


def sandbox_status() -> SandboxStatus:
    """Return the current status of the sandbox environment."""
    import json as _json

    result = subprocess.run(
        ["docker", "inspect", SANDBOX_CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return SandboxStatus(running=False)

    try:
        info = _json.loads(result.stdout)[0]
    except (IndexError, _json.JSONDecodeError):
        return SandboxStatus(running=False)

    running = info.get("State", {}).get("Running", False)
    container_id = info.get("Id", "")[:12]
    created_at = info.get("Created")
    image = info.get("Config", {}).get("Image", "")

    kubeconfig_str = str(SANDBOX_KUBECONFIG) if SANDBOX_KUBECONFIG.exists() else None

    return SandboxStatus(
        running=running,
        container_id=container_id,
        kubeconfig=kubeconfig_str,
        k3s_version=image,
        created_at=created_at,
    )
