"""Local QA sandbox: a k3s cluster plus a SkyPilot API server, in Docker.

Topology::

    host ──:16443──▶ [stratus-sandbox-k3s]  (k3s server, single node)
         ──:46580──▶      └─ shared netns ── [stratus-sandbox-skypilot]
                                              (sky api start --deploy)

The API server container joins the k3s container's network namespace, so it
reaches the Kubernetes API at ``127.0.0.1:6443`` and both ports are published
from the k3s container. SkyPilot is configured with the k3s kubeconfig, so
``infra: k8s`` launches pods inside the sandbox.

Host quirks handled automatically:

* cgroup v1 hosts: kubelet ≥1.35 refuses cgroup v1 unless told otherwise.
* hosts that forbid lowering ``oom_score_adj`` (nested/sandboxed Docker):
  containerd is switched to ``restrict_oom_score_adj``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from stratus.settings import load_settings, sandbox_dir, save_settings

K3S_CONTAINER = "stratus-sandbox-k3s"
SKY_CONTAINER = "stratus-sandbox-skypilot"
K3S_IMAGE = os.environ.get("STRATUS_SANDBOX_K3S_IMAGE", "rancher/k3s:v1.35.2-k3s1")
# Keep in lock-step with the `skypilot` client pin in pyproject.toml.
SKY_IMAGE = os.environ.get("STRATUS_SANDBOX_SKYPILOT_IMAGE", "berkeleyskypilot/skypilot:0.14.2")
# Default SkyPilot pod image for Kubernetes in SkyPilot 0.14.x (from its
# catalog). Pre-loading it makes the first launch fast and works on hosts whose
# k3s node cannot reach the registry directly.
POD_IMAGES = tuple(
    os.environ.get("STRATUS_SANDBOX_POD_IMAGES", "us-docker.pkg.dev/sky-dev-465/skypilotk8s/skypilot:202607240748").split(",")
)
KUBE_PORT = 16443
SERVE_PORTS = (30001, 30020)
API_PORT = 46580

Progress = Callable[[str, str], None]

STEPS = [
    "Docker",
    "Images",
    "k3s container",
    "Kubernetes node",
    "Kubernetes system pods",
    "Kubeconfig",
    "SkyPilot API server",
    "SkyPilot ↔ Kubernetes",
    "Pod image preload",
    "stratus config",
]


class SandboxError(Exception):
    pass


@dataclass(frozen=True)
class SandboxStatus:
    k3s: str | None
    skypilot: str | None
    endpoint: str
    healthy: bool
    server_version: str | None
    kubeconfig: Path | None

    @property
    def exists(self) -> bool:
        return self.k3s is not None or self.skypilot is not None


def _run(cmd: list[str], check: bool = True, input: str | None = None, timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, input=input, timeout=timeout)
    except FileNotFoundError as exc:
        raise SandboxError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxError(f"timed out: {' '.join(cmd)}") from exc
    if check and proc.returncode != 0:
        raise SandboxError(f"command failed: {' '.join(cmd)}\n{(proc.stderr or proc.stdout).strip()}")
    return proc


def _container_state(name: str) -> str | None:
    proc = _run(["docker", "inspect", "-f", "{{.State.Status}}", name], check=False)
    return proc.stdout.strip() if proc.returncode == 0 else None


def endpoint(api_port: int = API_PORT) -> str:
    return f"http://127.0.0.1:{api_port}"


def kubeconfig_path() -> Path:
    return sandbox_dir() / "kubeconfig"


def _internal_kubeconfig_path() -> Path:
    return sandbox_dir() / "kubeconfig.internal"


def _server_config_path() -> Path:
    return sandbox_dir() / "skypilot-config.yaml"


def _health(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=3) as resp:
            return json.loads(resp.read().decode() or "{}")
    except Exception:  # noqa: BLE001
        return None


def _wait(desc: str, check: Callable[[], bool], timeout: float, interval: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(interval)
    raise SandboxError(f"{desc}: not ready after {int(timeout)}s")


def _kubectl(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return _run(["docker", "exec", K3S_CONTAINER, "kubectl", *args], check=check)


def _needs_oom_workaround() -> bool:
    """True when containers may not lower oom_score_adj (pause pods would fail)."""
    proc = _run(
        ["docker", "run", "--rm", "--privileged", "--entrypoint", "sh", K3S_IMAGE, "-c", "echo -998 > /proc/self/oom_score_adj"],
        check=False,
        timeout=120,
    )
    return proc.returncode != 0


def _cgroup_v1() -> bool:
    proc = _run(["docker", "info", "--format", "{{.CgroupVersion}}"], check=False)
    return proc.stdout.strip() == "1"


def up(
    on_progress: Progress | None = None,
    *,
    api_port: int = API_PORT,
    kube_port: int = KUBE_PORT,
    ca_bundle: Path | None = None,
    cpus: float | None = None,
    memory_gb: float | None = None,
    set_default: bool = True,
    preload: bool = True,
) -> str:
    """Create the sandbox and return the API server endpoint.

    ``ca_bundle`` is for networks with a TLS-intercepting proxy: the file is
    trusted by k3s (image pulls) and by the API server (catalog downloads).
    """
    notify = on_progress or (lambda step, status: None)

    def step(name: str, fn: Callable[[], object]) -> object:
        notify(name, "running")
        try:
            result = fn()
        except Exception:
            notify(name, "failed")
            raise
        notify(name, "done")
        return result

    if shutil.which("docker") is None:
        notify("Docker", "failed")
        raise SandboxError("docker is not installed or not on PATH")
    step("Docker", lambda: _run(["docker", "info", "--format", "{{.ServerVersion}}"]))

    if _container_state(K3S_CONTAINER) or _container_state(SKY_CONTAINER):
        notify("Images", "failed")
        raise SandboxError("a sandbox already exists — run `stratus sandbox down` first (or `status`)")

    def pull() -> None:
        for image in (K3S_IMAGE, SKY_IMAGE):
            if _run(["docker", "image", "inspect", image], check=False).returncode != 0:
                _run(["docker", "pull", image], timeout=1800)

    step("Images", pull)

    sandbox_dir().mkdir(parents=True, exist_ok=True)
    ca = Path(ca_bundle).expanduser().resolve() if ca_bundle else None
    if ca and not ca.is_file():
        raise SandboxError(f"CA bundle not found: {ca}")

    oom_workaround = _needs_oom_workaround()
    cgroup_v1 = _cgroup_v1()

    def start_k3s() -> None:
        cmd = [
            "docker", "run", "-d", "--name", K3S_CONTAINER, "--privileged",
            "--label", "app=stratus-sandbox",
            "-p", f"127.0.0.1:{kube_port}:6443",
            "-p", f"127.0.0.1:{api_port}:{API_PORT}",
            # SkyServe load balancers run inside the API server (consolidation
            # mode) on ports 30001+; publish a range so endpoints work from the host.
            "-p", f"127.0.0.1:{SERVE_PORTS[0]}-{SERVE_PORTS[1]}:{SERVE_PORTS[0]}-{SERVE_PORTS[1]}",
            "-e", "K3S_KUBECONFIG_MODE=644",
        ]
        if cpus:
            cmd += ["--cpus", str(cpus)]
        if memory_gb:
            cmd += ["--memory", f"{memory_gb}g"]
        if ca:
            cmd += ["-v", f"{ca}:/etc/ssl/certs/ca-certificates.crt:ro"]
        cmd += [
            K3S_IMAGE, "server",
            "--disable=traefik",
            "--tls-san=127.0.0.1",
            # A sandbox lives on laptops with full disks; don't evict pods
            # or refuse images because the host is >85% used.
            "--kubelet-arg=eviction-hard=imagefs.available<1%,nodefs.available<1%",
            "--kubelet-arg=image-gc-high-threshold=100",
        ]
        if cgroup_v1:
            cmd.append("--kubelet-arg=fail-cgroupv1=false")
        _run(cmd)

    step("k3s container", start_k3s)

    try:
        def node_ready() -> bool:
            out = _kubectl("get", "nodes", "--no-headers").stdout
            return " Ready" in out

        def wait_node() -> None:
            _wait("k3s node", node_ready, 180)
            if oom_workaround:
                _apply_oom_workaround()
                _wait("k3s node after restart", node_ready, 180)

        step("Kubernetes node", wait_node)

        def system_pods() -> None:
            def ok() -> bool:
                out = _kubectl("get", "pods", "-n", "kube-system", "--no-headers").stdout
                return "coredns" in out and "local-path" in out and all(
                    " Running " in line or " Completed " in line for line in out.splitlines() if line.strip()
                ) and _deployment_ready("coredns") and _deployment_ready("local-path-provisioner")

            _wait("kube-system pods", ok, 300)

        step("Kubernetes system pods", system_pods)

        def kubeconfig() -> None:
            raw = _run(["docker", "exec", K3S_CONTAINER, "cat", "/etc/rancher/k3s/k3s.yaml"]).stdout
            _internal_kubeconfig_path().write_text(raw)
            kubeconfig_path().write_text(raw.replace("https://127.0.0.1:6443", f"https://127.0.0.1:{kube_port}"))
            _server_config_path().write_text(server_config(trust_ca=ca is not None))
            if ca is not None:
                # k3s already trusts the bundle (mounted over its system CAs);
                # publish it to pods as a ConfigMap referenced by pod_config.
                _kubectl("delete", "configmap", CA_CONFIGMAP, "-n", "default", "--ignore-not-found")
                _kubectl(
                    "create", "configmap", CA_CONFIGMAP, "-n", "default",
                    "--from-file=ca.crt=/etc/ssl/certs/ca-certificates.crt", check=True,
                )

        step("Kubeconfig", kubeconfig)

        def start_server() -> None:
            cmd = [
                "docker", "run", "-d", "--name", SKY_CONTAINER,
                "--label", "app=stratus-sandbox",
                "--network", f"container:{K3S_CONTAINER}",
                "-v", f"{_internal_kubeconfig_path()}:/root/.kube/config:ro",
                "-v", f"{_server_config_path()}:/root/.sky/config.yaml:ro",
            ]
            if ca:
                cmd += [
                    "-v", f"{ca}:/etc/stratus/ca.crt:ro",
                    "-e", "REQUESTS_CA_BUNDLE=/etc/stratus/ca.crt",
                    "-e", "SSL_CERT_FILE=/etc/stratus/ca.crt",
                    "-e", "CURL_CA_BUNDLE=/etc/stratus/ca.crt",
                ]
            cmd += [SKY_IMAGE, "sky", "api", "start", "--deploy", "--foreground", "--host", "0.0.0.0", "--port", str(API_PORT)]
            _run(cmd)
            url = endpoint(api_port)

            def healthy() -> bool:
                if _container_state(SKY_CONTAINER) not in ("running", "created"):
                    logs = _run(["docker", "logs", "--tail", "20", SKY_CONTAINER], check=False)
                    raise SandboxError(f"SkyPilot API server exited:\n{logs.stdout}{logs.stderr}")
                data = _health(url)
                return bool(data and data.get("status") == "healthy")

            _wait("SkyPilot API server", healthy, 300)

        step("SkyPilot API server", start_server)

        def sky_check() -> None:
            out = ""
            for _ in range(3):
                proc = _run(["docker", "exec", SKY_CONTAINER, "sky", "check", "kubernetes"], check=False, timeout=300)
                out = proc.stdout + proc.stderr
                if proc.returncode == 0 and "Kubernetes" in out and "Kubernetes: disabled" not in out:
                    return
                time.sleep(5)
            raise SandboxError(f"SkyPilot could not use the sandbox Kubernetes cluster:\n{out[-2000:]}")

        step("SkyPilot ↔ Kubernetes", sky_check)

        if preload:
            step("Pod image preload", lambda: preload_images(POD_IMAGES))
        else:
            notify("Pod image preload", "done")

        def configure() -> None:
            if set_default:
                settings = load_settings()
                settings.endpoint = endpoint(api_port)
                save_settings(settings)

        step("stratus config", configure)
    except BaseException:
        _remove_containers()
        raise
    return endpoint(api_port)


def preload_images(images: tuple[str, ...] | list[str] = POD_IMAGES) -> None:
    """Pull images with the host's Docker and import them into k3s containerd.

    The host's Docker cache survives ``sandbox down``, so recreating the
    sandbox doesn't re-download the multi-GB SkyPilot image.
    """
    if _container_state(K3S_CONTAINER) != "running":
        raise SandboxError("sandbox is not running")
    for image in images:
        if _run(["docker", "image", "inspect", image], check=False).returncode != 0:
            _run(["docker", "pull", image], timeout=3600)
        save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
        try:
            imp = subprocess.run(
                ["docker", "exec", "-i", K3S_CONTAINER, "ctr", "-n", "k8s.io", "images", "import", "-"],
                stdin=save.stdout, capture_output=True, text=True, timeout=3600,
            )
        finally:
            if save.stdout:
                save.stdout.close()
            save.wait()
        if save.returncode != 0 or imp.returncode != 0:
            raise SandboxError(f"failed to import {image} into k3s: {imp.stderr.strip()}")


CA_CONFIGMAP = "stratus-ca"
_POD_CA_DIR = "/etc/stratus-ca"


def server_config(trust_ca: bool = False) -> str:
    """SkyPilot server config (``~/.sky/config.yaml``) for the sandbox."""
    import yaml

    container: dict = {"imagePullPolicy": "IfNotPresent"}
    spec: dict = {"containers": [container]}
    if trust_ca:
        ca_file = f"{_POD_CA_DIR}/ca.crt"
        container["env"] = [
            {"name": name, "value": ca_file}
            for name in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_CERT")
        ]
        container["volumeMounts"] = [{"name": CA_CONFIGMAP, "mountPath": _POD_CA_DIR, "readOnly": True}]
        spec["volumes"] = [{"name": CA_CONFIGMAP, "configMap": {"name": CA_CONFIGMAP}}]
    config = {
        "kubernetes": {"allowed_contexts": ["default"], "pod_config": {"spec": spec}},
        # Run the managed-jobs and serve controllers inside the API server
        # instead of extra controller pods: lighter and faster in a sandbox.
        "jobs": {"controller": {"consolidation_mode": True}},
        "serve": {"controller": {"consolidation_mode": True}},
    }
    header = (
        "# SkyPilot API server config for the stratus sandbox.\n"
        "# imagePullPolicy IfNotPresent: use images loaded by `stratus sandbox preload`.\n"
    )
    return header + yaml.safe_dump(config, sort_keys=False)


def _deployment_ready(name: str) -> bool:
    out = _kubectl("get", "deployment", name, "-n", "kube-system", "-o", "jsonpath={.status.readyReplicas}").stdout.strip()
    return out not in ("", "0")


def _apply_oom_workaround() -> None:
    """Make containerd clamp oom_score_adj instead of failing pod sandboxes."""
    base = "/var/lib/rancher/k3s/agent/etc/containerd"
    _wait(
        "containerd config",
        lambda: _run(["docker", "exec", K3S_CONTAINER, "test", "-f", f"{base}/config.toml"], check=False).returncode == 0,
        60,
        1,
    )
    script = (
        f"sed \"/^\\[plugins.'io.containerd.cri.v1.runtime'\\]$/a\\  restrict_oom_score_adj = true\" "
        f"{base}/config.toml > {base}/config-v3.toml.tmpl && grep -q restrict_oom_score_adj {base}/config-v3.toml.tmpl"
    )
    _run(["docker", "exec", K3S_CONTAINER, "sh", "-c", script])
    _run(["docker", "restart", K3S_CONTAINER])


def _remove_containers() -> None:
    for name in (SKY_CONTAINER, K3S_CONTAINER):
        _run(["docker", "rm", "-f", "-v", name], check=False)


def down() -> bool:
    """Remove the sandbox. Returns False if there was nothing to remove."""
    existed = bool(_container_state(K3S_CONTAINER) or _container_state(SKY_CONTAINER))
    _remove_containers()
    for path in (kubeconfig_path(), _internal_kubeconfig_path(), _server_config_path()):
        path.unlink(missing_ok=True)
    settings = load_settings()
    if settings.endpoint and settings.endpoint.startswith("http://127.0.0.1:") and settings.endpoint.endswith(str(API_PORT)):
        settings.endpoint = None
        save_settings(settings)
    return existed


def status(api_port: int = API_PORT) -> SandboxStatus:
    url = endpoint(api_port)
    data = _health(url) if _container_state(SKY_CONTAINER) == "running" else None
    kc = kubeconfig_path()
    return SandboxStatus(
        k3s=_container_state(K3S_CONTAINER),
        skypilot=_container_state(SKY_CONTAINER),
        endpoint=url,
        healthy=bool(data and data.get("status") == "healthy"),
        server_version=(data or {}).get("version"),
        kubeconfig=kc if kc.exists() else None,
    )


def server_logs(tail: int = 200) -> str:
    proc = _run(["docker", "logs", "--tail", str(tail), SKY_CONTAINER], check=False)
    return proc.stdout + proc.stderr
