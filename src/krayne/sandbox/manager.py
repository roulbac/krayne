from __future__ import annotations

import glob
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from krayne.config.settings import (
    PRISM_DIR,
    clear_krayne_settings,
    load_krayne_settings,
    save_krayne_settings,
    KrayneSettings,
)
from krayne.errors import (
    ConfigValidationError,
    DockerNotFoundError,
    SandboxAlreadyExistsError,
    SandboxError,
    SandboxNotFoundError,
)

SANDBOX_CONTAINER_NAME = "krayne-sandbox"
K3S_IMAGE = "rancher/k3s:v1.34.8-k3s1"
HELM_IMAGE = "alpine/helm"
KUBERAY_CHART_VERSION = "1.6.1"
KUBERAY_CHART_URL = (
    "https://github.com/ray-project/kuberay-helm/releases/download/"
    f"kuberay-operator-{KUBERAY_CHART_VERSION}/"
    f"kuberay-operator-{KUBERAY_CHART_VERSION}.tgz"
)
SANDBOX_KUBECONFIG = PRISM_DIR / "sandbox-kubeconfig"

MIN_CPUS = 2
MIN_MEMORY_GB = 4
SANDBOX_MEMORY_GB = 6

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


def _wait_until(
    check_fn: Callable[[], bool],
    step_name: str,
    timeout: int,
    on_progress: ProgressCallback,
    poll_interval: float = 3.0,
    timeout_message: str | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        _notify(on_progress, step_name, "in_progress")
        if check_fn():
            _notify(on_progress, step_name, "done")
            return
        time.sleep(poll_interval)
    raise SandboxError(
        timeout_message or f"{step_name} not ready within {timeout}s"
    )


def _k3s_node_ready() -> bool:
    result = subprocess.run(
        [
            "docker", "exec", SANDBOX_CONTAINER_NAME,
            "kubectl", "get", "nodes",
            "-o", "jsonpath={.items[0].status.conditions[-1].type}",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and "Ready" in result.stdout


def _raycluster_crd_registered() -> bool:
    result = subprocess.run(
        [
            "docker", "exec", SANDBOX_CONTAINER_NAME,
            "kubectl", "get", "crd", "rayclusters.ray.io",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _deployment_available(name: str, namespace: str) -> bool:
    result = subprocess.run(
        [
            "docker", "exec", SANDBOX_CONTAINER_NAME,
            "kubectl", "get", "deployment", name,
            "-n", namespace,
            "-o", "jsonpath={.status.availableReplicas}",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() not in ("", "0", "null")


def _check_docker(on_progress: ProgressCallback) -> None:
    _notify(on_progress, STEP_DOCKER, "in_progress")
    try:
        result = _run(["docker", "info", "--format", "{{.NCPU}} {{.MemTotal}}"])
    except SandboxError:
        _notify(on_progress, STEP_DOCKER, "failed")
        raise DockerNotFoundError()

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
                "Increase resources in your Docker/Rancher Desktop settings."
            )
    _notify(on_progress, STEP_DOCKER, "done")


def _build_ca_bundle() -> str | None:
    """Merge host CA bundle with extra certs (e.g. corporate proxy CAs).

    Returns the path to a merged bundle file, or None if no extra CAs exist.
    """
    extra_certs = glob.glob("/usr/local/share/ca-certificates/*.crt")
    if not extra_certs:
        return None
    host_bundle = Path("/etc/ssl/certs/ca-certificates.crt")
    if not host_bundle.exists():
        return None
    merged = PRISM_DIR / "sandbox-ca-certificates.crt"
    PRISM_DIR.mkdir(parents=True, exist_ok=True)
    parts = [host_bundle.read_text()]
    for cert_file in extra_certs:
        parts.append(Path(cert_file).read_text())
    merged.write_text("\n".join(parts))
    return str(merged)


_RUNC_WRAPPER = """\
#!/bin/sh
# Neutralise negative oomScoreAdj values that runc's nsexec cannot apply
# inside nested pid-namespaces on cgroup-v1 hosts (kernel 6.x+).
bundle=""
is_create=0
prev=""
for arg in "$@"; do
  case "$arg" in create) is_create=1 ;; --bundle=*) bundle="${arg#--bundle=}" ;; esac
  [ "$prev" = "--bundle" ] && bundle="$arg"
  prev="$arg"
done
if [ "$is_create" = "1" ] && [ -n "$bundle" ] && [ -f "$bundle/config.json" ]; then
  sed -i 's/"oomScoreAdj":-[0-9]*/"oomScoreAdj":0/g' "$bundle/config.json"
fi
exec /bin/runc.real "$@"
"""


def _install_runc_wrapper() -> None:
    """Swap runc with a wrapper that zeroes negative oomScoreAdj values.

    On cgroup-v1 hosts with kernel >= 6.x, runc 1.4+ fails to set negative
    oom_score_adj inside nested pid-namespaces (``nsexec: failed to update
    /proc/self/oom_score_adj: Permission denied``).  The wrapper patches the
    OCI config before delegating to the real binary.
    """
    result = subprocess.run(
        ["docker", "exec", SANDBOX_CONTAINER_NAME, "stat", "/sys/fs/cgroup/cgroup.controllers"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        return
    subprocess.run(
        ["docker", "exec", SANDBOX_CONTAINER_NAME,
         "sh", "-c", "cp /bin/runc /bin/runc.real"],
        capture_output=True, text=True, check=True,
    )
    subprocess.run(
        ["docker", "exec", SANDBOX_CONTAINER_NAME,
         "sh", "-c", f"cat > /bin/runc << 'WRAPPER_EOF'\n{_RUNC_WRAPPER}WRAPPER_EOF\nchmod +x /bin/runc"],
        capture_output=True, text=True, check=True,
    )


def _start_k3s_container(on_progress: ProgressCallback) -> None:
    if _container_exists():
        _notify(on_progress, STEP_K3S_CONTAINER, "failed")
        raise SandboxAlreadyExistsError()

    _notify(on_progress, STEP_K3S_CONTAINER, "in_progress")
    ca_bundle = _build_ca_bundle()
    ca_args = ["-v", f"{ca_bundle}:/etc/ssl/certs/ca-certificates.crt:ro"] if ca_bundle else []
    _run([
        "docker", "run", "-d",
        "--name", SANDBOX_CONTAINER_NAME,
        "--privileged",
        "-p", "6443:6443",
        "-p", "30000-30100:30000-30100",
        "--cpus", str(MIN_CPUS),
        "--memory", f"{SANDBOX_MEMORY_GB}g",
        "-e", "K3S_KUBECONFIG_MODE=644",
        *ca_args,
        K3S_IMAGE,
        "server", "--disable=traefik",
        "--kube-apiserver-arg", "service-node-port-range=30000-30100",
    ])
    _install_runc_wrapper()
    _notify(on_progress, STEP_K3S_CONTAINER, "done")


def _extract_kubeconfig(on_progress: ProgressCallback) -> str:
    _notify(on_progress, STEP_KUBECONFIG, "in_progress")
    result = _run([
        "docker", "exec", SANDBOX_CONTAINER_NAME,
        "cat", "/etc/rancher/k3s/k3s.yaml",
    ])
    raw_kubeconfig = result.stdout

    PRISM_DIR.mkdir(parents=True, exist_ok=True)
    SANDBOX_KUBECONFIG.write_text(raw_kubeconfig)
    _notify(on_progress, STEP_KUBECONFIG, "done")
    return raw_kubeconfig


def _install_kuberay(raw_kubeconfig: str, on_progress: ProgressCallback) -> None:
    _notify(on_progress, STEP_HELM_INSTALL, "in_progress")
    internal_kubeconfig = str(PRISM_DIR / "sandbox-kubeconfig-internal")
    Path(internal_kubeconfig).write_text(raw_kubeconfig)

    chart_path = str(PRISM_DIR / f"kuberay-operator-{KUBERAY_CHART_VERSION}.tgz")
    _run(["curl", "-fsSL", "-o", chart_path, KUBERAY_CHART_URL])

    try:
        _run([
            "docker", "run", "--rm",
            "--network", f"container:{SANDBOX_CONTAINER_NAME}",
            "-v", f"{internal_kubeconfig}:/root/.kube/config:ro",
            "-v", f"{chart_path}:/tmp/chart.tgz:ro",
            HELM_IMAGE,
            "install", "kuberay-operator", "/tmp/chart.tgz",
            "--namespace", "default",
        ])
    finally:
        Path(internal_kubeconfig).unlink(missing_ok=True)
        Path(chart_path).unlink(missing_ok=True)
    _notify(on_progress, STEP_HELM_INSTALL, "done")


def setup_sandbox(on_progress: ProgressCallback = None) -> str:
    """Create a local k3s container with KubeRay and return the kubeconfig path."""
    _check_docker(on_progress)
    _start_k3s_container(on_progress)

    try:
        _wait_until(
            _k3s_node_ready,
            STEP_K3S_NODE,
            timeout=120,
            on_progress=on_progress,
            timeout_message="K3S node not ready within 120s",
        )

        raw_kubeconfig = _extract_kubeconfig(on_progress)
        _install_kuberay(raw_kubeconfig, on_progress)

        kubeconfig_path = str(SANDBOX_KUBECONFIG)

        _wait_until(
            _raycluster_crd_registered,
            STEP_CRD,
            timeout=120,
            on_progress=on_progress,
            timeout_message="RayCluster CRD not registered within timeout",
        )

        _wait_until(
            lambda: _deployment_available("kuberay-operator", "default"),
            STEP_OPERATOR,
            timeout=180,
            on_progress=on_progress,
            poll_interval=5.0,
            timeout_message="Deployment kuberay-operator not available within 180s",
        )

        save_krayne_settings(KrayneSettings(kubeconfig=kubeconfig_path))
        return kubeconfig_path

    except Exception:
        subprocess.run(
            ["docker", "rm", "-f", SANDBOX_CONTAINER_NAME],
            capture_output=True,
            text=True,
        )
        raise


def teardown_sandbox() -> None:
    if not _container_exists():
        raise SandboxNotFoundError()

    _run(["docker", "rm", "-f", SANDBOX_CONTAINER_NAME])

    if SANDBOX_KUBECONFIG.exists():
        SANDBOX_KUBECONFIG.unlink()

    # A ConfigValidationError here means the settings file is already in
    # a broken state (e.g. references a missing kubeconfig) — treat
    # that as "clear the settings" since the sandbox kubeconfig is
    # about to disappear anyway.
    try:
        settings = load_krayne_settings()
    except ConfigValidationError:
        clear_krayne_settings()
    else:
        if settings.kubeconfig == str(SANDBOX_KUBECONFIG):
            clear_krayne_settings()


def sandbox_status() -> SandboxStatus:
    result = subprocess.run(
        ["docker", "inspect", SANDBOX_CONTAINER_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return SandboxStatus(running=False)

    try:
        info = json.loads(result.stdout)[0]
    except (IndexError, json.JSONDecodeError):
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
