"""Backend implementation on top of the SkyPilot Python SDK (API-server mode).

SkyPilot SDK calls return request ids; listing calls are resolved with
``sky.get`` inside TUI worker threads, mutations return the request id so the
TUI can follow progress in a log view.

``sky`` is imported lazily — it is slow to import and must see
``SKYPILOT_API_SERVER_ENDPOINT`` before it reads its configuration.
"""

from __future__ import annotations

import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from stratus.backend.base import (
    BackendError,
    ClusterJobRow,
    ClusterRow,
    CostRow,
    PoolRow,
    StorageRow,
    WorkspaceRow,
    InfraRow,
    LogSink,
    ManagedJobRow,
    ReplicaRow,
    RequestRow,
    ServerInfo,
    ServerUnavailableError,
    ServiceRow,
    VolumeRow,
)
from stratus.settings import ENDPOINT_ENV
from stratus.specs.models import ClusterSpec, JobSpec, PoolSpec, ServiceSpec, Spec, VolumeSpec

_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# Requests SkyPilot runs for itself; hidden from the Requests view.
_INTERNAL_REQUEST = re.compile(r"(daemon|on-boot|heartbeat|cleanup)", re.IGNORECASE)


def _g(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from a pydantic response model or a dict."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    value = getattr(obj, key, default)
    return default if value is None else value


def _enum(value: Any) -> str:
    if value is None:
        return "-"
    return str(getattr(value, "value", value))


def _clean(msg: str) -> str:
    return _ANSI.sub("", msg).strip()


class _Stopped(Exception):
    pass


class _SinkStream(io.TextIOBase):
    """A text stream that forwards complete lines to a sink callback."""

    def __init__(self, sink: LogSink, stop: threading.Event) -> None:
        super().__init__()
        self._sink = sink
        self._stop = stop
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:  # type: ignore[override]
        if self._stop.is_set():
            raise _Stopped()
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._sink(line.rstrip("\r"))
        # Spinners redraw with \r; keep only the latest frame.
        if "\r" in self._buf:
            self._buf = self._buf.rsplit("\r", 1)[-1]
        return len(s)

    def flush(self) -> None:
        # Never raise here: IOBase's finalizer calls flush(); stopping is
        # signalled from write(), which the SDK calls for every chunk.
        return None

    def close_out(self) -> None:
        if self._buf.strip():
            self._sink(self._buf)
        self._buf = ""


class SkyPilotBackend:
    def __init__(self, endpoint: str, all_users: bool = False, workspace: str | None = None) -> None:
        self.endpoint = endpoint.rstrip("/")
        os.environ[ENDPOINT_ENV] = self.endpoint
        self.all_users = all_users
        self._sky: Any = None
        self._health: tuple[float, ServerInfo] | None = None
        self._lock = threading.Lock()
        if workspace:
            _write_workspace_overlay(workspace)

    # ── plumbing ─────────────────────────────────────────────

    @property
    def sky(self) -> Any:
        if self._sky is None:
            with self._lock:
                if self._sky is None:
                    import sky  # noqa: PLC0415 - deliberate lazy import

                    self._sky = sky
        return self._sky

    def server_info(self) -> ServerInfo:
        now = time.monotonic()
        if self._health and now - self._health[0] < 2.0:
            return self._health[1]
        info = self._probe()
        self._health = (now, info)
        return info

    def _probe(self) -> ServerInfo:
        try:
            with urllib.request.urlopen(f"{self.endpoint}/api/health", timeout=3) as resp:
                data = json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return ServerInfo(self.endpoint, False, error="authentication required — run `sky api login`")
            return ServerInfo(self.endpoint, False, error=f"HTTP {exc.code}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return ServerInfo(self.endpoint, False, error=f"unreachable ({reason})")
        user = data.get("user")
        if isinstance(user, dict):
            user = user.get("name") or user.get("id")
        return ServerInfo(
            endpoint=self.endpoint,
            healthy=data.get("status") == "healthy",
            version=data.get("version"),
            api_version=str(data.get("api_version")) if data.get("api_version") is not None else None,
            user=user,
            error=None if data.get("status") == "healthy" else str(data.get("status")),
        )

    def _require_server(self) -> None:
        # Never let the SDK auto-start a local API server behind our back:
        # check health ourselves first.
        info = self.server_info()
        if not info.healthy:
            raise ServerUnavailableError(f"SkyPilot API server at {self.endpoint} is {info.error or 'not healthy'}")

    def _call(self, fn, *args, **kwargs) -> Any:
        """Submit a request and return its request id."""
        self._require_server()
        try:
            return str(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001 - SDK raises many types
            raise BackendError(_clean(str(exc)) or type(exc).__name__) from exc

    def _get(self, fn, *args, **kwargs) -> Any:
        """Submit a request and block for its result."""
        self._require_server()
        try:
            return self.sky.get(fn(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            raise BackendError(_clean(str(exc)).splitlines()[-1] if str(exc).strip() else type(exc).__name__) from exc

    # ── listing ──────────────────────────────────────────────

    def list_clusters(self, refresh: bool = False) -> list[ClusterRow]:
        sky = self.sky
        mode = sky.StatusRefreshMode.FORCE if refresh else sky.StatusRefreshMode.NONE
        records = self._get(sky.status, refresh=mode, all_users=self.all_users)
        rows = []
        for r in records or []:
            rows.append(
                ClusterRow(
                    name=_g(r, "name"),
                    status=_enum(_g(r, "status")),
                    infra=_cluster_infra(r),
                    resources=_g(r, "resources_str") or "-",
                    launched_at=_g(r, "launched_at"),
                    autostop=int(_g(r, "autostop", -1)),
                    to_down=bool(_g(r, "to_down", False)),
                    user=_g(r, "user_name") or "-",
                    workspace=_g(r, "workspace") or "default",
                    last_event=_g(r, "last_event"),
                    creation_yaml=_g(r, "last_creation_yaml"),
                    details={
                        "resources_full": _g(r, "resources_str_full"),
                        "last_creation_command": _g(r, "last_creation_command"),
                        "status_updated_at": _g(r, "status_updated_at"),
                        "init_status_reason": _g(r, "init_status_reason"),
                        "is_managed": _g(r, "is_managed"),
                    },
                )
            )
        return rows

    def list_cluster_jobs(self, cluster: str) -> list[ClusterJobRow]:
        records = self._get(self.sky.queue, cluster, skip_finished=False, all_users=self.all_users)
        return [
            ClusterJobRow(
                cluster=cluster,
                job_id=int(_g(r, "job_id")),
                name=_g(r, "job_name") or "-",
                status=_enum(_g(r, "status")),
                submitted_at=_g(r, "submitted_at"),
                start_at=_g(r, "start_at"),
                end_at=_g(r, "end_at"),
                resources=_g(r, "resources") or "-",
                user=_g(r, "username") or "-",
            )
            for r in records or []
        ]

    def list_managed_jobs(self) -> list[ManagedJobRow]:
        import sky.jobs  # noqa: PLC0415

        queue = getattr(sky.jobs, "queue_v2", None)
        if queue is not None:
            result = self._get(queue, refresh=False, skip_finished=False, all_users=self.all_users, fields=None)
        else:  # older SkyPilot
            result = self._get(sky.jobs.queue, refresh=False, skip_finished=False, all_users=self.all_users)
        records = result[0] if isinstance(result, tuple) else result
        rows = []
        for r in records or []:
            rows.append(
                ManagedJobRow(
                    job_id=int(_g(r, "job_id") or 0),
                    name=_g(r, "job_name") or _g(r, "task_name") or "-",
                    status=_enum(_g(r, "status")),
                    resources=_g(r, "resources") or "-",
                    infra=_g(r, "infra") or _g(r, "cloud") or "-",
                    submitted_at=_g(r, "submitted_at"),
                    duration=_g(r, "job_duration"),
                    recoveries=int(_g(r, "recovery_count", 0) or 0),
                    user=_g(r, "user_name") or "-",
                    details=_g(r, "failure_reason") or _g(r, "details"),
                    user_yaml=_g(r, "user_yaml"),
                )
            )
        return rows

    def list_services(self) -> list[ServiceRow]:
        import sky.serve  # noqa: PLC0415

        records = self._get(sky.serve.status, None)
        rows = []
        for r in records or []:
            replicas = _replicas(r)
            ready = sum(1 for rep in replicas if rep.status == "READY")
            versions = ",".join(str(v) for v in r.get("active_versions") or []) or "-"
            rows.append(
                ServiceRow(
                    name=r["name"],
                    status=_enum(r.get("status")),
                    endpoint=r.get("endpoint"),
                    ready_replicas=ready,
                    total_replicas=len(replicas),
                    versions=versions,
                    uptime=r.get("uptime"),
                    replicas=tuple(replicas),
                    yaml=r.get("yaml_content"),
                )
            )
        return rows

    def list_pools(self) -> list[PoolRow]:
        import sky.jobs  # noqa: PLC0415

        records = self._get(sky.jobs.pool_status, None)
        rows = []
        for r in records or []:
            workers = _replicas(r)
            rows.append(
                PoolRow(
                    name=r["name"],
                    status=_enum(r.get("status")),
                    ready_workers=sum(1 for w in workers if w.status == "READY"),
                    total_workers=len(workers),
                    resources=r.get("requested_resources_str") or "-",
                    workers=tuple(workers),
                    yaml=r.get("yaml_content"),
                )
            )
        return rows

    def list_costs(self, days: int | None = 30) -> list[CostRow]:
        from sky.client import sdk  # noqa: PLC0415

        records = self._get(sdk.cost_report, days=days)
        rows = []
        for r in records or []:
            user = r.get("user_name") or "-"
            nodes = int(r.get("num_nodes") or 1)
            res = r.get("resources")
            cloud = str(r.get("cloud") or getattr(res, "cloud", "") or "-")
            region = r.get("region")
            accel = r.get("accelerators") or getattr(res, "accelerators", None) or {}
            gpus = float(sum(accel.values())) * nodes if isinstance(accel, dict) else 0.0
            duration = float(r.get("duration") or 0)
            try:
                hourly: float | None = float(res.get_cost(3600)) * nodes if res is not None else None
            except Exception:  # noqa: BLE001
                hourly = None
            total: float | None = float(r.get("total_cost") or 0.0)
            # Kubernetes is $0 unless the server configures `kubernetes.pricing`;
            # report that as unpriced rather than free.
            if cloud.lower().startswith("kubernetes") and not hourly:
                hourly, total = None, None
            rows.append(
                CostRow(
                    name=r.get("name") or "-",
                    status=_enum(r.get("status")) if r.get("status") else "TERMINATED",
                    infra=f"{cloud} ({region})" if region else cloud,
                    resources=r.get("resources_str") or "-",
                    num_nodes=nodes,
                    gpus=gpus,
                    launched_at=r.get("launched_at"),
                    duration=duration,
                    hourly=hourly,
                    total=total,
                    user=user,
                    workspace=r.get("workspace") or "default",
                )
            )
        rows.sort(key=lambda c: -(c.launched_at or 0))
        return rows

    def list_storage(self) -> list[StorageRow]:
        from sky.client import sdk  # noqa: PLC0415

        records = self._get(sdk.storage_ls)
        rows = []
        for r in records or []:
            stores = _g(r, "store", []) or []
            rows.append(
                StorageRow(
                    name=_g(r, "name"),
                    stores=", ".join(_enum(x) for x in stores) or "-",
                    status=_enum(_g(r, "status")),
                    launched_at=_g(r, "launched_at"),
                    last_use=_g(r, "last_use") or "-",
                )
            )
        return rows

    def list_workspaces(self) -> list[WorkspaceRow]:
        from sky import skypilot_config  # noqa: PLC0415
        from sky.client import sdk  # noqa: PLC0415

        workspaces = self._get(sdk.workspaces) or {}
        active = skypilot_config.get_active_workspace()
        rows = []
        for name, cfg in workspaces.items():
            cfg = cfg if isinstance(cfg, dict) else {}
            bits = []
            if cfg.get("private"):
                bits.append("private")
            if cfg.get("read_only"):
                bits.append("read-only")
            clouds = [k for k in cfg if k not in ("private", "allowed_users", "writable", "read_only")]
            if clouds:
                bits.append("clouds: " + ", ".join(clouds))
            rows.append(WorkspaceRow(name=name, active=name == active, writable=bool(cfg.get("writable", True)), detail="; ".join(bits)))
        return rows

    def set_workspace(self, name: str) -> None:
        """Make ``name`` the active workspace for this stratus session.

        Uses a SkyPilot *project* config overlay (``active_workspace``), so
        the user's ~/.sky/config.yaml is never modified.
        """
        _write_workspace_overlay(name)
        try:
            from sky.client import sdk  # noqa: PLC0415

            sdk.reload_config()
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"could not switch workspace: {_clean(str(exc))}") from exc

    def list_volumes(self) -> list[VolumeRow]:
        import sky.volumes  # noqa: PLC0415

        records = self._get(sky.volumes.ls)
        return [
            VolumeRow(
                name=_g(r, "name"),
                type=_g(r, "type") or "-",
                infra=_volume_infra(r),
                size=_volume_size(_g(r, "size")),
                status=_enum(_g(r, "status")),
                used_by=tuple(_g(r, "usedby_clusters", []) or []) + tuple(_g(r, "usedby_pods", []) or []),
                launched_at=_g(r, "launched_at"),
                user=_g(r, "user_name") or "-",
                creation_yaml=_g(r, "creation_yaml"),
            )
            for r in records or []
        ]

    def list_requests(self, limit: int = 100) -> list[RequestRow]:
        self._require_server()
        try:
            records = self.sky.api_status(all_status=True, limit=limit)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(_clean(str(exc))) from exc
        rows = []
        for r in records or []:
            name = _g(r, "name") or "-"
            if _INTERNAL_REQUEST.search(name):
                continue
            rows.append(
                RequestRow(
                    request_id=_g(r, "request_id"),
                    name=name,
                    status=_enum(_g(r, "status")),
                    created_at=_g(r, "created_at"),
                    finished_at=_g(r, "finished_at"),
                    cluster=_g(r, "cluster_name"),
                    user=_g(r, "user_name") or "-",
                    status_msg=_g(r, "status_msg"),
                )
            )
        rows.sort(key=lambda x: x.created_at or 0, reverse=True)
        return rows

    def list_infra(self) -> list[InfraRow]:
        from sky.client import sdk  # noqa: PLC0415

        enabled = self._get(sdk.enabled_clouds, expand=True)
        return [InfraRow(name=str(c), enabled=True, detail=_infra_detail(str(c))) for c in enabled or []]

    # ── clusters ─────────────────────────────────────────────

    def _task(self, yaml_dict: dict[str, Any]) -> Any:
        try:
            return self.sky.Task.from_yaml_config(yaml_dict)
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"invalid task: {_clean(str(exc))}") from exc

    def launch_cluster(self, spec: ClusterSpec) -> str:
        task = self._task(spec.to_yaml_dict())
        return self._call(self.sky.launch, task, cluster_name=spec.name)

    def exec_on_cluster(self, cluster: str, command: str) -> str:
        task = self._task({"name": "stratus-exec", "run": command})
        return self._call(self.sky.exec, task, cluster_name=cluster)

    def stop_cluster(self, cluster: str) -> str:
        return self._call(self.sky.stop, cluster)

    def start_cluster(self, cluster: str) -> str:
        return self._call(self.sky.start, cluster)

    def down_cluster(self, cluster: str) -> str:
        return self._call(self.sky.down, cluster)

    def set_autostop(self, cluster: str, minutes: int, down: bool) -> str:
        return self._call(self.sky.autostop, cluster, minutes, down=down)

    def cancel_cluster_job(self, cluster: str, job_id: int) -> str:
        return self._call(self.sky.cancel, cluster, job_ids=[job_id])

    def ssh_argv(self, cluster: str) -> list[str]:
        """Write the SSH config entry for ``cluster`` and return ``ssh`` argv.

        SkyPilot's CLI does this as part of ``sky status``; in API-server mode
        Kubernetes clusters are reached through a websocket proxy.
        """
        import shutil  # noqa: PLC0415

        if shutil.which("ssh") is None:
            raise BackendError("no `ssh` client on PATH — install OpenSSH to open shells")
        self._require_server()
        try:
            from sky.client.cli import command as sky_cli  # noqa: PLC0415

            sky_cli._get_cluster_records_and_set_ssh_config(clusters=[cluster])  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"could not prepare SSH for {cluster}: {_clean(str(exc))}") from exc
        return ["ssh", cluster]

    # ── managed jobs ─────────────────────────────────────────

    def launch_managed_job(self, spec: JobSpec) -> str:
        import sky.jobs  # noqa: PLC0415

        task = self._task(spec.to_yaml_dict())
        if spec.pool:
            return self._call(sky.jobs.launch, task, name=spec.name, pool=spec.pool)
        return self._call(sky.jobs.launch, task, name=spec.name)

    def preview_launch(self, spec: Spec) -> str:
        if isinstance(spec, VolumeSpec):
            raise BackendError("volumes have no launch preview")
        data = spec.to_yaml_dict()
        # The optimizer only needs the task's resources; drop serve/pool sections.
        data.pop("service", None)
        data.pop("pool", None)
        task = self._task(data)
        return self._call(self.sky.launch, task, dryrun=True)

    def cancel_managed_job(self, job_id: int) -> str:
        import sky.jobs  # noqa: PLC0415

        return self._call(sky.jobs.cancel, job_ids=[job_id])

    # ── services ─────────────────────────────────────────────

    def serve_up(self, spec: ServiceSpec) -> str:
        import sky.serve  # noqa: PLC0415

        task = self._task(spec.to_yaml_dict())
        return self._call(sky.serve.up, task, spec.name)

    def serve_update(self, spec: ServiceSpec) -> str:
        import sky.serve  # noqa: PLC0415

        task = self._task(spec.to_yaml_dict())
        return self._call(sky.serve.update, task, spec.name, mode=sky.serve.UpdateMode.ROLLING)

    def serve_down(self, name: str) -> str:
        import sky.serve  # noqa: PLC0415

        return self._call(sky.serve.down, name)

    def terminate_replica(self, service: str, replica_id: int) -> str:
        import sky.serve  # noqa: PLC0415

        return self._call(sky.serve.terminate_replica, service, replica_id, False)

    # ── volumes ──────────────────────────────────────────────

    def pool_apply(self, spec: PoolSpec) -> str:
        import sky.jobs  # noqa: PLC0415
        import sky.serve  # noqa: PLC0415

        task = self._task(spec.to_yaml_dict())
        return self._call(sky.jobs.pool_apply, task, spec.name, mode=sky.serve.UpdateMode.ROLLING)

    def pool_down(self, name: str) -> str:
        import sky.jobs  # noqa: PLC0415

        return self._call(sky.jobs.pool_down, name)

    def delete_storage(self, name: str) -> str:
        from sky.client import sdk  # noqa: PLC0415

        return self._call(sdk.storage_delete, name)

    def apply_volume(self, spec: VolumeSpec) -> str:
        import sky.volumes  # noqa: PLC0415

        try:
            volume = sky.volumes.Volume.from_yaml_config(spec.to_yaml_dict())
        except Exception as exc:  # noqa: BLE001
            raise BackendError(f"invalid volume: {_clean(str(exc))}") from exc
        return self._call(sky.volumes.apply, volume)

    def delete_volume(self, name: str) -> str:
        import sky.volumes  # noqa: PLC0415

        return self._call(sky.volumes.delete, [name])

    # ── requests & logs ──────────────────────────────────────

    def cancel_request(self, request_id: str) -> None:
        self._get(self.sky.api_cancel, request_id)

    def _stream(self, sink: LogSink, stop: threading.Event, fn, *args, **kwargs) -> None:
        self._require_server()
        stream = _SinkStream(sink, stop)
        try:
            fn(*args, output_stream=stream, **kwargs)
        except _Stopped:
            return
        except Exception as exc:  # noqa: BLE001
            stream.close_out()
            if not stop.is_set():
                sink(f"[stratus] {_clean(str(exc)) or type(exc).__name__}")
            return
        stream.close_out()

    def stream_request_logs(self, request_id: str, sink: LogSink, stop: threading.Event) -> None:
        self._stream(sink, stop, self.sky.stream_and_get, request_id, follow=True)

    def stream_cluster_job_logs(self, cluster: str, job_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        self._stream(sink, stop, self.sky.tail_logs, cluster, job_id, True)

    def stream_managed_job_logs(
        self, job_id: int, sink: LogSink, stop: threading.Event, controller: bool = False, follow: bool = True
    ) -> None:
        import sky.jobs  # noqa: PLC0415

        # Following a finished job only prints "already in terminal state".
        self._stream(sink, stop, sky.jobs.tail_logs, job_id=job_id, follow=follow, controller=controller)

    def stream_pool_logs(self, pool: str, target: str, worker_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        import sky.jobs  # noqa: PLC0415

        self._stream(sink, stop, sky.jobs.pool_tail_logs, pool, target=target, worker_id=worker_id, follow=True)

    def stream_service_logs(self, service: str, target: str, replica_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        import sky.serve  # noqa: PLC0415

        self._stream(sink, stop, sky.serve.tail_logs, service, target=target, replica_id=replica_id, follow=True)


def _infra_detail(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("kubernetes"):
        ctx = name.split("/", 1)[1] if "/" in name else "current context"
        return f"use infra: k8s/{ctx}" if "/" in name else "use infra: k8s"
    return f"use infra: {lowered}"


def _cluster_infra(record: Any) -> str:
    handle = _g(record, "handle")
    infra = _handle_infra(handle)
    if infra != "-":
        return infra
    for key in ("infra", "cloud"):
        if _g(record, key):
            return str(_g(record, key))
    res = _g(record, "resources_str") or ""
    m = re.search(r"x\s*([A-Za-z]+)\(", res)
    return m.group(1) if m else "-"


def _handle_infra(handle: Any) -> str:
    try:
        res = handle.launched_resources
        infra = getattr(res, "infra", None)
        if infra is not None:
            text = infra.formatted_str() if hasattr(infra, "formatted_str") else str(infra)
            return text or "-"
        return str(res.cloud)
    except Exception:  # noqa: BLE001
        return "-"


def _handle_resources(handle: Any) -> str:
    try:
        res = handle.launched_resources
        nodes = getattr(handle, "launched_nodes", 1)
        return f"{nodes}x {res.cloud}({res.instance_type})"
    except Exception:  # noqa: BLE001
        return "-"


def _volume_size(size: Any) -> str:
    if size is None or size == "":
        return "-"
    text = str(size)
    return f"{text}Gi" if text.replace(".", "", 1).isdigit() else text  # same as `sky volumes ls`


def _volume_infra(record: Any) -> str:
    parts = [str(_g(record, k)) for k in ("cloud", "region", "zone") if _g(record, k)]
    return "/".join(parts) or "-"


def _replicas(record: dict) -> list[ReplicaRow]:
    """Replica (service) or worker (pool) rows from a serve/pool status record."""
    out = []
    for info in record.get("replica_info") or []:
        handle = info.get("handle")
        out.append(
            ReplicaRow(
                service=record["name"],
                replica_id=int(info.get("replica_id", 0)),
                status=_enum(info.get("status")),
                version=info.get("version"),
                infra=info.get("infra") or _handle_infra(handle),
                resources=info.get("resources_str") or _handle_resources(handle),
                endpoint=info.get("endpoint"),
                launched_at=info.get("launched_at"),
            )
        )
    return out


def workspace_overlay_path():
    from stratus.settings import stratus_home  # noqa: PLC0415

    return stratus_home() / "skypilot-workspace.yaml"


def _write_workspace_overlay(workspace: str) -> None:
    """Point SkyPilot's project-config layer at a file setting active_workspace.

    If the user already has a project config (env var or ./.sky.yaml), its
    content is carried over so nothing they configured is lost.
    """
    import yaml  # noqa: PLC0415

    from pathlib import Path  # noqa: PLC0415

    target = workspace_overlay_path()
    base: dict = {}
    existing = os.environ.get("SKYPILOT_PROJECT_CONFIG")
    candidates = [Path(existing)] if existing and Path(existing) != target else [Path.cwd() / ".sky.yaml"]
    for path in candidates:
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text()) or {}
                if isinstance(loaded, dict):
                    base = loaded
            except yaml.YAMLError:
                pass
    base["active_workspace"] = workspace
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Written by stratus to select the SkyPilot workspace.\n" + yaml.safe_dump(base, sort_keys=False))
    os.environ["SKYPILOT_PROJECT_CONFIG"] = str(target)
