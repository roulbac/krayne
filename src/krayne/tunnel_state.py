"""Declarative tunnel-state storage.

The state file at ``~/.krayne/tunnels/<ns>/<cluster>.json`` is the source of
truth for which tunnels should be open. CLI/SDK writes ``desired_tunnels``;
the per-cluster manager subprocess owns ``status`` + ``manager`` and
reconciles observed forwards against the desired set.

All reads and writes go through an exclusive ``fcntl.flock`` on the file so
the CLI and the manager don't race. Linux/macOS only; advisory.
"""

from __future__ import annotations

import contextlib
import dataclasses
import enum
import fcntl
import json
import os
import signal
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from krayne.config.settings import PRISM_DIR

TUNNEL_DIR = PRISM_DIR / "tunnels"

SCHEMA_VERSION = 2


class ServiceState(str, enum.Enum):
    PENDING = "pending"   # Desired entry seen; not yet bound
    OPEN = "open"         # Listener bound and last accept loop healthy
    BROKEN = "broken"     # Transient — under restart window
    FAILED = "failed"     # Exceeded restart budget; manager won't retry


@dataclass(frozen=True)
class TunnelInfo:
    """Metadata for a single port-forward tunnel."""

    service: str
    remote_port: int
    local_port: int
    local_url: str


@dataclass
class ManagerInfo:
    pid: int
    start_time: float
    heartbeat: float


@dataclass
class ServiceStatus:
    state: ServiceState = ServiceState.PENDING
    pod: str | None = None
    restart_count: int = 0
    last_restart: float | None = None
    last_error: str | None = None


@dataclass
class KubeConfigRef:
    kubeconfig: str | None = None
    context: str | None = None


@dataclass
class TunnelState:
    cluster_name: str
    namespace: str
    desired_tunnels: list[TunnelInfo] = field(default_factory=list)
    kube_config: KubeConfigRef = field(default_factory=KubeConfigRef)
    manager: ManagerInfo | None = None
    status: dict[str, ServiceStatus] = field(default_factory=dict)

    @property
    def tunnels(self) -> list[TunnelInfo]:
        """Backwards-compat alias for ``desired_tunnels``.

        Existing callers (`format_tunnel_panel`, `cli/submit.py`, the TUI)
        read ``state.tunnels`` — preserve that surface.
        """
        return self.desired_tunnels


def state_path(cluster_name: str, namespace: str) -> Path:
    return TUNNEL_DIR / namespace / f"{cluster_name}.json"


def log_path(cluster_name: str, namespace: str) -> Path:
    return TUNNEL_DIR / namespace / f"{cluster_name}.log"


@contextlib.contextmanager
def _open_locked(path: Path, *, exclusive: bool = True) -> Iterator:
    """Open *path* for read+write under an advisory ``fcntl.flock``.

    Creates the file (and parent dir) if missing. Yields a file object
    positioned at offset 0. Lock is released on context exit.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            fh = os.fdopen(fd, "r+", encoding="utf-8")
            fd = -1  # ownership transferred to fh; don't double-close
            try:
                fh.seek(0)
                yield fh
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fh.close()
        finally:
            if fd >= 0:
                # Lock is released on close.
                os.close(fd)
    except Exception:
        if fd >= 0:
            os.close(fd)
        raise


def load_state(cluster_name: str, namespace: str) -> TunnelState | None:
    """Read and parse the state file. Returns ``None`` if absent or stale.

    Legacy v1 files (``pids: [...]``) are reaped — every PID listed gets
    a ``SIGTERM`` and the file is removed. The caller's next write creates
    a fresh v2 file.
    """
    path = state_path(cluster_name, namespace)
    if not path.exists():
        return None
    try:
        with _open_locked(path, exclusive=True) as fh:
            raw_text = fh.read()
            if not raw_text.strip():
                return None
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError:
                # Truncate-and-discard while we hold the lock.
                fh.seek(0)
                fh.truncate(0)
                path.unlink(missing_ok=True)
                return None
            if raw.get("schema_version") != SCHEMA_VERSION:
                # Legacy v1: SIGTERM any recorded kubectl PIDs, discard.
                for pid in raw.get("pids", []) or []:
                    with contextlib.suppress(OSError, TypeError, ValueError):
                        os.kill(int(pid), signal.SIGTERM)
                fh.seek(0)
                fh.truncate(0)
                path.unlink(missing_ok=True)
                return None
            return _decode(raw)
    except FileNotFoundError:
        return None


def save_state(state: TunnelState) -> None:
    """Persist *state* under an exclusive lock."""
    path = state_path(state.cluster_name, state.namespace)
    with _open_locked(path, exclusive=True) as fh:
        fh.seek(0)
        fh.truncate(0)
        json.dump(_encode(state), fh, indent=2)


def update(
    cluster_name: str,
    namespace: str,
    mutator,
) -> TunnelState:
    """Atomically load-mutate-save the state file.

    *mutator* receives the current :class:`TunnelState` (or a freshly
    initialised one if the file is missing) and may modify it in place
    or return a new instance. The result is persisted before the lock
    is released, so concurrent CLI / manager updates serialise.
    """
    path = state_path(cluster_name, namespace)
    with _open_locked(path, exclusive=True) as fh:
        raw_text = fh.read()
        state: TunnelState
        if raw_text.strip():
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError:
                raw = {}
            if raw.get("schema_version") == SCHEMA_VERSION:
                state = _decode(raw)
            else:
                # Legacy or corrupt: reap and start fresh under the lock.
                for pid in raw.get("pids", []) or []:
                    with contextlib.suppress(OSError, TypeError, ValueError):
                        os.kill(int(pid), signal.SIGTERM)
                state = TunnelState(cluster_name=cluster_name, namespace=namespace)
        else:
            state = TunnelState(cluster_name=cluster_name, namespace=namespace)

        result = mutator(state) or state
        fh.seek(0)
        fh.truncate(0)
        json.dump(_encode(result), fh, indent=2)
        return result


def delete_state(cluster_name: str, namespace: str) -> bool:
    """Remove the state file. Returns ``True`` if it existed."""
    path = state_path(cluster_name, namespace)
    if not path.exists():
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def _encode(state: TunnelState) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "cluster_name": state.cluster_name,
        "namespace": state.namespace,
        "kube_config": {
            "kubeconfig": state.kube_config.kubeconfig,
            "context": state.kube_config.context,
        },
        "desired_tunnels": [dataclasses.asdict(t) for t in state.desired_tunnels],
        "manager": (
            {
                "pid": state.manager.pid,
                "start_time": state.manager.start_time,
                "heartbeat": state.manager.heartbeat,
            }
            if state.manager is not None
            else None
        ),
        "status": {
            svc: {
                "state": s.state.value,
                "pod": s.pod,
                "restart_count": s.restart_count,
                "last_restart": s.last_restart,
                "last_error": s.last_error,
            }
            for svc, s in state.status.items()
        },
    }


def _decode(raw: dict) -> TunnelState:
    kube_raw = raw.get("kube_config") or {}
    mgr_raw = raw.get("manager")
    return TunnelState(
        cluster_name=raw["cluster_name"],
        namespace=raw["namespace"],
        desired_tunnels=[TunnelInfo(**t) for t in raw.get("desired_tunnels", [])],
        kube_config=KubeConfigRef(
            kubeconfig=kube_raw.get("kubeconfig"),
            context=kube_raw.get("context"),
        ),
        manager=(
            ManagerInfo(
                pid=int(mgr_raw["pid"]),
                start_time=float(mgr_raw["start_time"]),
                heartbeat=float(mgr_raw["heartbeat"]),
            )
            if mgr_raw is not None
            else None
        ),
        status={
            svc: ServiceStatus(
                state=ServiceState(s.get("state", "pending")),
                pod=s.get("pod"),
                restart_count=int(s.get("restart_count", 0)),
                last_restart=s.get("last_restart"),
                last_error=s.get("last_error"),
            )
            for svc, s in (raw.get("status") or {}).items()
        },
    )


def manager_alive(info: ManagerInfo | None, *, heartbeat_max_age: float = 10.0) -> bool:
    """Return True iff the manager process is alive AND its heartbeat is fresh.

    Uses ``psutil.Process.create_time()`` as a fingerprint to defend
    against PID recycling (long uptime, reboots).
    """
    if info is None:
        return False
    if time.time() - info.heartbeat > heartbeat_max_age:
        return False
    try:
        import psutil

        proc = psutil.Process(info.pid)
        # create_time may drift by a tick across psutil versions; allow 1s slop.
        if abs(proc.create_time() - info.start_time) > 1.0:
            return False
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except Exception:
        return False


def wait_until_open(
    cluster_name: str,
    namespace: str,
    services: set[str],
    *,
    timeout: float = 30.0,
    interval: float = 0.2,
) -> dict[str, ServiceStatus]:
    """Poll the state file until every service in *services* is ``OPEN``
    (or ``FAILED``), returning the per-service status snapshot.

    Returns even if some services are ``FAILED`` — the caller decides
    whether to error. Raises :class:`TimeoutError` only when *timeout*
    expires before all services settle.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, ServiceStatus] = {}
    while True:
        state = load_state(cluster_name, namespace)
        if state is not None:
            last = state.status
            settled = {
                svc for svc, st in last.items()
                if st.state in (ServiceState.OPEN, ServiceState.FAILED)
            }
            if services.issubset(settled):
                return last
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Tunnel manager for '{cluster_name}/{namespace}' did not "
                f"report all services open within {timeout:.0f}s "
                f"(current: {last!r})"
            )
        time.sleep(interval)


# Re-export commonly imported error type for callers that don't want to
# add their own except clause for the rare disk full / EIO case.
__all__ = [
    "KubeConfigRef",
    "ManagerInfo",
    "SCHEMA_VERSION",
    "ServiceState",
    "ServiceStatus",
    "TUNNEL_DIR",
    "TunnelInfo",
    "TunnelState",
    "delete_state",
    "load_state",
    "log_path",
    "manager_alive",
    "save_state",
    "state_path",
    "update",
    "wait_until_open",
]


