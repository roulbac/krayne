from __future__ import annotations

import functools
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anyio

from prism._async_utils import _run_sync
from prism.config.settings import (
    PRISM_DIR,
    PrismSettings,
    _async_clear_prism_settings,
    _async_load_prism_settings,
    _async_save_prism_settings,
    clear_prism_settings,
    load_prism_settings,
    save_prism_settings,
)
from prism.errors import (
    DockerNotFoundError,
    SandboxAlreadyExistsError,
    SandboxError,
    SandboxNotFoundError,
)

SANDBOX_CONTAINER_NAME = "prism-sandbox"
K3S_IMAGE = "rancher/k3s:v1.35.2-k3s1"
HELM_IMAGE = "alpine/helm"
KUBERAY_HELM_REPO = "https://ray-project.github.io/kuberay-helm"
SANDBOX_KUBECONFIG = PRISM_DIR / "sandbox-kubeconfig"

MIN_CPUS = 2
MIN_MEMORY_GB = 4

# Step names for progress reporting
STEP_DOCKER = "Docker"
STEP_K3S_CONTAINER = "K3S Container"
STEP_K3S_NODE = "K3S Node"
STEP_KUBECONFIG = "Kubeconfig"
STEP_HELM_INSTALL = "KubeRay Helm Chart"
STEP_CRD = "RayCluster CRD"
STEP_OPERATOR = "Operator Ready"

SETUP_STEPS = [
    STEP_DOCKER,
    STEP_K3S_CONTAINER,
    STEP_K3S_NODE,
    STEP_KUBECONFIG,
    STEP_HELM_INSTALL,
    STEP_CRD,
    STEP_OPERATOR,
]

ProgressCallback = Callable[[str, str], None] | None


@dataclass(frozen=True)
class SandboxStatus:
    running: bool
    container_id: str | None = None
    kubeconfig: str | None = None
    k3s_version: str | None = None
    created_at: str | None = None


# ---------------------------------------------------------------------------
# Low-level helpers (sync, used by async wrappers via thread offload)
# ---------------------------------------------------------------------------


def _run(cmd: list[str], check: bool = True, **kwargs) -> subprocess.CompletedProcess:
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
    result = subprocess.run(
        ["docker", "inspect", SANDBOX_CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _notify(on_progress: ProgressCallback, step: str, status: str) -> None:
    if on_progress is not None:
        on_progress(step, status)


# ---------------------------------------------------------------------------
# Async internal implementations
# ---------------------------------------------------------------------------


async def _async_run(
    cmd: list[str], check: bool = True, **kwargs
) -> subprocess.CompletedProcess:
    return await anyio.to_thread.run_sync(
        functools.partial(_run, cmd, check=check, **kwargs)
    )


async def _async_container_exists() -> bool:
    return await anyio.to_thread.run_sync(_container_exists)


async def _async_wait_for_k3s(
    timeout: int = 120, on_progress: ProgressCallback = None
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _notify(on_progress, STEP_K3S_NODE, "in_progress")
        result = await anyio.to_thread.run_sync(
            functools.partial(
                subprocess.run,
                [
                    "docker", "exec", SANDBOX_CONTAINER_NAME,
                    "kubectl", "get", "nodes",
                    "-o", "jsonpath={.items[0].status.conditions[-1].type}",
                ],
                capture_output=True,
                text=True,
            )
        )
        if result.returncode == 0 and "Ready" in result.stdout:
            return
        await anyio.sleep(3)
    raise SandboxError(f"K3S node not ready within {timeout}s")


async def _async_wait_for_crds(
    kubeconfig: str, timeout: int = 120, on_progress: ProgressCallback = None
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _notify(on_progress, STEP_CRD, "in_progress")
        result = await anyio.to_thread.run_sync(
            functools.partial(
                subprocess.run,
                ["kubectl", "--kubeconfig", kubeconfig, "get", "crd", "rayclusters.ray.io"],
                capture_output=True,
                text=True,
            )
        )
        if result.returncode == 0:
            return
        await anyio.sleep(3)
    raise SandboxError("RayCluster CRD not registered within timeout")


async def _async_wait_for_deployment(
    name: str,
    kubeconfig: str,
    namespace: str = "default",
    timeout: int = 180,
    on_progress: ProgressCallback = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _notify(on_progress, STEP_OPERATOR, "in_progress")
        result = await anyio.to_thread.run_sync(
            functools.partial(
                subprocess.run,
                [
                    "kubectl", "--kubeconfig", kubeconfig,
                    "get", "deployment", name,
                    "-n", namespace,
                    "-o", "jsonpath={.status.availableReplicas}",
                ],
                capture_output=True,
                text=True,
            )
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "0", "null"):
            return
        await anyio.sleep(5)
    raise SandboxError(f"Deployment {name} not available within {timeout}s")


async def _async_setup_sandbox(on_progress: ProgressCallback = None) -> str:
    """Async implementation of :func:`setup_sandbox`."""
    # 1. Check Docker
    _notify(on_progress, STEP_DOCKER, "in_progress")
    try:
        result = await _async_run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"])
    except SandboxError:
        _notify(on_progress, STEP_DOCKER, "failed")
        raise DockerNotFoundError()

    # Validate Docker has enough resources
    parts = result.stdout.strip().split()
    if len(parts) >= 2:
        ncpu = int(parts[0])
        mem_bytes = int(parts[1])
        mem_gb = mem_bytes / (1024 ** 3)
        if ncpu < MIN_CPUS or mem_gb < MIN_MEMORY_GB:
            _notify(on_progress, STEP_DOCKER, "failed")
            raise SandboxError(
                f"Docker has {ncpu} CPUs and {mem_gb:.1f}GB memory, "
                f"but the sandbox requires at least {MIN_CPUS} CPUs "
                f"and {MIN_MEMORY_GB}GB memory. "
                f"Increase resources in your Docker/Rancher Desktop settings."
            )
    _notify(on_progress, STEP_DOCKER, "done")

    # 2. Check for existing sandbox
    if await _async_container_exists():
        _notify(on_progress, STEP_K3S_CONTAINER, "failed")
        raise SandboxAlreadyExistsError()

    # 3. Start k3s
    _notify(on_progress, STEP_K3S_CONTAINER, "in_progress")
    await _async_run([
        "docker", "run", "-d",
        "--name", SANDBOX_CONTAINER_NAME,
        "--privileged",
        "-p", "6443:6443",
        "-p", "30000-30100:30000-30100",
        "--cpus", str(MIN_CPUS),
        "--memory", f"{MIN_MEMORY_GB}g",
        "-e", "K3S_KUBECONFIG_MODE=644",
        K3S_IMAGE,
        "server", "--disable=traefik",
        "--kube-apiserver-arg", "service-node-port-range=30000-30100",
    ])
    _notify(on_progress, STEP_K3S_CONTAINER, "done")

    try:
        # 4. Wait for k3s node
        _notify(on_progress, STEP_K3S_NODE, "in_progress")
        await _async_wait_for_k3s(on_progress=on_progress)
        _notify(on_progress, STEP_K3S_NODE, "done")

        # 5. Extract kubeconfig
        _notify(on_progress, STEP_KUBECONFIG, "in_progress")
        result = await _async_run([
            "docker", "exec", SANDBOX_CONTAINER_NAME,
            "cat", "/etc/rancher/k3s/k3s.yaml",
        ])
        raw_kubeconfig = result.stdout

        # Write host kubeconfig (127.0.0.1:6443 reachable via port mapping)
        await anyio.Path(PRISM_DIR).mkdir(parents=True, exist_ok=True)
        await anyio.Path(SANDBOX_KUBECONFIG).write_text(raw_kubeconfig)
        _notify(on_progress, STEP_KUBECONFIG, "done")

        # 6. Install KubeRay via Helm (shares k3s container network)
        _notify(on_progress, STEP_HELM_INSTALL, "in_progress")
        internal_kubeconfig = str(PRISM_DIR / "sandbox-kubeconfig-internal")
        await anyio.Path(internal_kubeconfig).write_text(raw_kubeconfig)
        try:
            await _async_run([
                "docker", "run", "--rm",
                "--network", f"container:{SANDBOX_CONTAINER_NAME}",
                "-v", f"{internal_kubeconfig}:/root/.kube/config:ro",
                HELM_IMAGE,
                "install", "kuberay-operator", "kuberay-operator",
                "--repo", KUBERAY_HELM_REPO,
                "--namespace", "default",
            ])
        finally:
            await anyio.Path(internal_kubeconfig).unlink(missing_ok=True)
        _notify(on_progress, STEP_HELM_INSTALL, "done")

        # 7. Wait for CRD + operator
        kubeconfig_path = str(SANDBOX_KUBECONFIG)

        _notify(on_progress, STEP_CRD, "in_progress")
        await _async_wait_for_crds(kubeconfig_path, on_progress=on_progress)
        _notify(on_progress, STEP_CRD, "done")

        _notify(on_progress, STEP_OPERATOR, "in_progress")
        await _async_wait_for_deployment(
            "kuberay-operator", kubeconfig_path, on_progress=on_progress
        )
        _notify(on_progress, STEP_OPERATOR, "done")

        # 8. Save as active config
        await _async_save_prism_settings(PrismSettings(kubeconfig=kubeconfig_path))

        return kubeconfig_path

    except Exception:
        # Clean up the container on any setup failure
        await anyio.to_thread.run_sync(
            functools.partial(
                subprocess.run,
                ["docker", "rm", "-f", SANDBOX_CONTAINER_NAME],
                capture_output=True,
                text=True,
            )
        )
        raise


async def _async_teardown_sandbox() -> None:
    """Async implementation of :func:`teardown_sandbox`."""
    if not await _async_container_exists():
        raise SandboxNotFoundError()

    await _async_run(["docker", "rm", "-f", SANDBOX_CONTAINER_NAME])

    # Remove sandbox kubeconfig
    kube_path = anyio.Path(SANDBOX_KUBECONFIG)
    if await kube_path.exists():
        await kube_path.unlink()

    # Clear prism settings if they point to the sandbox
    settings = await _async_load_prism_settings()
    if settings.kubeconfig == str(SANDBOX_KUBECONFIG):
        await _async_clear_prism_settings()


async def _async_sandbox_status() -> SandboxStatus:
    """Async implementation of :func:`sandbox_status`."""
    import json as _json

    result = await anyio.to_thread.run_sync(
        functools.partial(
            subprocess.run,
            ["docker", "inspect", SANDBOX_CONTAINER_NAME],
            capture_output=True,
            text=True,
        )
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

    kube_path = anyio.Path(SANDBOX_KUBECONFIG)
    kubeconfig_str = str(SANDBOX_KUBECONFIG) if await kube_path.exists() else None

    return SandboxStatus(
        running=running,
        container_id=container_id,
        kubeconfig=kubeconfig_str,
        k3s_version=image,
        created_at=created_at,
    )


# ---------------------------------------------------------------------------
# Public sync API (unchanged signatures)
# ---------------------------------------------------------------------------


def setup_sandbox(on_progress: ProgressCallback = None) -> str:
    """Create a local k3s container with KubeRay and return the kubeconfig path."""
    return _run_sync(_async_setup_sandbox, on_progress=on_progress)


def teardown_sandbox() -> None:
    _run_sync(_async_teardown_sandbox)


def sandbox_status() -> SandboxStatus:
    return _run_sync(_async_sandbox_status)
