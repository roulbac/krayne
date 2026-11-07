"""An in-memory SkyPilot simulation for ``stratus --demo`` and tests.

State transitions are driven by wall-clock time so the UI shows realistic
progress (INIT → UP, PENDING → RUNNING → SUCCEEDED, ...) without a server.
Set ``speed`` > 1 to make everything happen faster.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from stratus.backend.base import (
    BackendError,
    CostRow,
    PoolRow,
    StorageRow,
    WorkspaceRow,
    ClusterJobRow,
    ClusterRow,
    InfraRow,
    LogSink,
    ManagedJobRow,
    ReplicaRow,
    RequestRow,
    ServerInfo,
    ServiceRow,
    VolumeRow,
)
from stratus.specs.models import ClusterSpec, JobSpec, PoolSpec, ServiceSpec, Spec, VolumeSpec

# Demo price list ($/hr per node). Kubernetes is deliberately absent:
# without `kubernetes.pricing` on the server, SkyPilot can't price it.
_PRICES = {"AWS": 0.096, "GCP": 3.67}

INFRA = ["k8s", "aws", "gcp"]


@dataclass
class _Request:
    request_id: str
    name: str
    cluster: str | None
    created_at: float
    duration: float
    lines: list[str]
    fail: str | None = None
    cancelled: bool = False
    on_done: Any = None
    done_fired: bool = False

    def status(self, now: float) -> str:
        if self.cancelled:
            return "CANCELLED"
        if now - self.created_at < self.duration:
            return "RUNNING"
        return "FAILED" if self.fail else "SUCCEEDED"


@dataclass
class _Cluster:
    name: str
    spec_yaml: str
    infra: str
    resources: str
    launched_at: float
    status: str = "INIT"
    autostop: int = -1
    to_down: bool = False
    jobs: list[ClusterJobRow] = field(default_factory=list)
    owner: str | None = None  # None = the current user
    gpus: float = 0.0
    nodes: int = 1


class FakeBackend:
    def __init__(self, speed: float = 1.0, seed: bool = True, user: str = "demo") -> None:
        self.endpoint = "demo://in-memory"
        self.speed = speed
        self.user = user
        self._lock = threading.RLock()
        self._requests: dict[str, _Request] = {}
        self._clusters: dict[str, _Cluster] = {}
        self._mjobs: dict[int, dict[str, Any]] = {}
        self._services: dict[str, dict[str, Any]] = {}
        self._volumes: dict[str, VolumeRow] = {}
        self._pools: dict[str, dict[str, Any]] = {}
        self._storage: dict[str, StorageRow] = {}
        self._history: list[CostRow] = []
        self._workspaces = {"default": "", "research": "clouds: aws, gcp"}
        self._workspace = "default"
        self.all_users = False
        self._job_ids = itertools.count(1)
        self.healthy = True
        if seed:
            self._seed()

    # ── helpers ──────────────────────────────────────────────

    def _t(self, seconds: float) -> float:
        return seconds / self.speed

    def _now(self) -> float:
        return time.time()

    def _submit(self, name: str, cluster: str | None, duration: float, lines: list[str], on_done=None, fail: str | None = None) -> str:
        rid = uuid.uuid4().hex[:8] + "-" + uuid.uuid4().hex[:4]
        with self._lock:
            self._requests[rid] = _Request(rid, name, cluster, self._now(), self._t(duration), lines, fail=fail, on_done=on_done)
        return rid

    def _tick(self) -> None:
        now = self._now()
        with self._lock:
            for req in self._requests.values():
                if not req.done_fired and req.status(now) == "SUCCEEDED":
                    req.done_fired = True
                    if req.on_done:
                        req.on_done()
            for c in self._clusters.values():
                for i, job in enumerate(c.jobs):
                    if job.status == "PENDING" and now - (job.submitted_at or now) > self._t(1):
                        c.jobs[i] = replace(job, status="RUNNING", start_at=now)
                    elif job.status == "RUNNING" and job.start_at and now - job.start_at > self._t(8):
                        c.jobs[i] = replace(job, status="SUCCEEDED", end_at=now)
            for j in self._mjobs.values():
                age = now - j["submitted_at"]
                if j["status"] in ("CANCELLING",):
                    j["status"] = "CANCELLED"
                elif j["status"] in ("CANCELLED", "FAILED", "SUCCEEDED"):
                    continue
                elif age > self._t(25):
                    j["status"] = "SUCCEEDED"
                elif age > self._t(6):
                    j["status"] = "RUNNING"
                elif age > self._t(2):
                    j["status"] = "STARTING"
            for s in self._services.values():
                age = now - s["created"]
                if s["status"] == "SHUTTING_DOWN":
                    continue
                reps = s["replicas"]
                for i, rep in enumerate(reps):
                    rage = now - (rep.launched_at or now)
                    if rep.status == "PROVISIONING" and rage > self._t(4):
                        reps[i] = replace(rep, status="STARTING")
                    elif rep.status == "STARTING" and rage > self._t(8):
                        reps[i] = replace(rep, status="READY", endpoint=f"http://10.0.0.{rep.replica_id + 10}:8080")
                if any(r.status == "READY" for r in reps):
                    s["status"] = "READY"
                elif age > self._t(2):
                    s["status"] = "REPLICA_INIT"

    def _seed(self) -> None:
        now = self._now()
        self._clusters["dev-gpu"] = _Cluster(
            "dev-gpu",
            "resources:\n  infra: k8s\n  accelerators: L4:1\n",
            "Kubernetes/default",
            "1x Kubernetes(4CPU--16GB--L4:1)",
            now - 3 * 3600,
            status="UP",
            autostop=30,
            jobs=[
                ClusterJobRow("dev-gpu", 1, "setup-env", "SUCCEEDED", now - 3 * 3600, now - 3 * 3600, now - 3 * 3500, "1x[L4:1]", self.user),
                ClusterJobRow("dev-gpu", 2, "train-lora", "RUNNING", now - 900, now - 880, None, "1x[L4:1]", self.user),
            ],
        )
        self._clusters["cpu-box"] = _Cluster(
            "cpu-box", "resources:\n  infra: aws\n  cpus: 2\n", "AWS (us-east-1)", "1x AWS(m6i.large)", now - 86400 * 2, status="STOPPED"
        )
        self._mjobs[7] = {
            "job_id": 7, "name": "nightly-eval", "status": "SUCCEEDED", "resources": "1x[A100:1]", "infra": "GCP/us-central1",
            "submitted_at": now - 7200, "end": now - 3600, "recoveries": 1, "yaml": "name: nightly-eval\nrun: python eval.py\n",
        }
        self._mjobs[8] = {
            "job_id": 8, "name": "pretrain-7b", "status": "RUNNING", "resources": "4x[H100:8]", "infra": "AWS/us-east-1",
            "submitted_at": now - 1800, "end": None, "recoveries": 0, "yaml": "name: pretrain-7b\nnum_nodes: 4\nrun: torchrun train.py\n",
        }
        self._job_ids = itertools.count(9)
        self._services["llm-api"] = {
            "name": "llm-api", "status": "READY", "created": now - 5000, "yaml": "service:\n  replicas: 2\n",
            "replicas": [
                ReplicaRow("llm-api", 1, "READY", 1, "Kubernetes/default", "1x Kubernetes(L4:1)", "http://10.0.0.11:8080", now - 5000),
                ReplicaRow("llm-api", 2, "READY", 1, "Kubernetes/default", "1x Kubernetes(L4:1)", "http://10.0.0.12:8080", now - 4900),
            ],
        }
        self._clusters["dev-gpu"].gpus = 1
        self._clusters["alice-train"] = _Cluster(
            "alice-train", "resources:\n  infra: gcp\n  accelerators: A100:1\n", "GCP (us-central1)", "1x GCP(a2-highgpu-1g)",
            now - 7 * 3600, status="UP", owner="alice", gpus=1,
        )
        self._history = [
            CostRow("sweep-a100", "TERMINATED", "GCP (us-central1)", "2x GCP(a2-highgpu-8g)", 2, 16, now - 3 * 86400, 5 * 3600,
                    2 * 29.39, 5 * 2 * 29.39, self.user),
            CostRow("k8s-finetune", "TERMINATED", "Kubernetes (default)", "1x Kubernetes(8CPU--64GB--H100:4)", 1, 4, now - 86400,
                    9 * 3600, None, None, self.user),
        ]
        self._storage["ckpt-bucket"] = StorageRow("ckpt-bucket", "S3", "READY", now - 86400 * 9, "sky launch -c sweep-a100 sweep.yaml")
        self._storage["sky-tmp-3f2a"] = StorageRow("sky-tmp-3f2a", "GCS", "READY", now - 86400 * 40, "sky jobs launch train.yaml")
        self._pools["eval-pool"] = {
            "name": "eval-pool", "status": "READY", "resources": "1x[CPU:2]", "yaml": "pool:\n  workers: 2\n",
            "workers": [ReplicaRow("eval-pool", i, "READY", 1, "Kubernetes/default", "1x Kubernetes(2CPU--4GB)", None, now - 3600) for i in (1, 2)],
        }
        self._volumes["datasets"] = VolumeRow("datasets", "k8s-pvc", "kubernetes/default", "100Gi", "IN_USE", ("dev-gpu",), now - 86400 * 5, self.user)

    # ── Backend API ──────────────────────────────────────────

    def server_info(self) -> ServerInfo:
        if not self.healthy:
            return ServerInfo(self.endpoint, False, error="unreachable (simulated)")
        return ServerInfo(self.endpoint, True, version="demo", api_version="-", user=self.user)

    def _check(self) -> None:
        if not self.healthy:
            from stratus.backend.base import ServerUnavailableError

            raise ServerUnavailableError("demo server is down")
        self._tick()

    def _visible(self, c: _Cluster) -> bool:
        return self.all_users or c.owner is None

    def list_clusters(self, refresh: bool = False) -> list[ClusterRow]:
        self._check()
        with self._lock:
            return [
                ClusterRow(
                    name=c.name, status=c.status, infra=c.infra, resources=c.resources, launched_at=c.launched_at,
                    autostop=c.autostop, to_down=c.to_down, user=c.owner or self.user, creation_yaml=c.spec_yaml,
                    last_event="Cluster is up." if c.status == "UP" else None,
                )
                for c in self._clusters.values()
                if self._visible(c)
            ]

    def list_costs(self, days: int | None = 30) -> list[CostRow]:
        self._check()
        now = self._now()
        rows = []
        with self._lock:
            for c in self._clusters.values():
                if not self._visible(c):
                    continue
                cloud = c.infra.split()[0].split("/")[0]
                price = _PRICES.get(cloud)
                up = (now - c.launched_at) if c.status in ("UP", "INIT") else 0.0
                hourly = price * c.nodes if price is not None else None
                rows.append(CostRow(
                    c.name, c.status, c.infra, c.resources, c.nodes, c.gpus * c.nodes, c.launched_at, up,
                    hourly, (hourly * up / 3600) if hourly is not None else None, c.owner or self.user,
                ))
            cutoff = now - days * 86400 if days else 0
            rows += [h for h in self._history if (h.launched_at or 0) >= cutoff]
        return sorted(rows, key=lambda r: -(r.launched_at or 0))

    def list_pools(self) -> list[PoolRow]:
        self._check()
        with self._lock:
            out = []
            for p in self._pools.values():
                workers = tuple(p["workers"])
                out.append(PoolRow(p["name"], p["status"], sum(1 for w in workers if w.status == "READY"), len(workers),
                                   p["resources"], workers, p["yaml"]))
            return out

    def list_storage(self) -> list[StorageRow]:
        self._check()
        with self._lock:
            return sorted(self._storage.values(), key=lambda r: r.name)

    def list_workspaces(self) -> list[WorkspaceRow]:
        self._check()
        return [WorkspaceRow(n, n == self._workspace, True, d) for n, d in self._workspaces.items()]

    def set_workspace(self, name: str) -> None:
        if name not in self._workspaces:
            raise BackendError(f"workspace {name!r} does not exist")
        self._workspace = name

    def preview_launch(self, spec: Spec) -> str:
        self._check()
        if isinstance(spec, VolumeSpec):
            raise BackendError("volumes have no launch preview")
        res = spec.resources
        n = spec.num_nodes
        lines = [f"Considered resources ({n} node{'s' if n > 1 else ''}):", "-" * 78,
                 " INFRA                  INSTANCE          vCPUs   Mem(GB)   GPUS     COST ($)   CHOSEN", "-" * 78]
        gpu = res.accelerators or "-"
        cands = [("Kubernetes (default)", "-", 0.0)]
        if res.infra is None or res.infra.startswith(("aws", "gcp")):
            cands += [("AWS (us-east-1)", "m6i.large" if gpu == "-" else "g6.xlarge", 0.096 if gpu == "-" else 0.805),
                      ("GCP (us-central1)", "n2-standard-2" if gpu == "-" else "g2-standard-4", 0.097 if gpu == "-" else 0.71)]
        if res.infra and res.infra.startswith("k8s"):
            cands = cands[:1]
        for i, (infra, inst, price) in enumerate(cands):
            cost = f"{price * n:.2f}"
            lines.append(f" {infra:<22} {inst:<17} {res.cpus or '-':<7} {res.memory or '-':<9} {gpu:<8} {cost:<10} {'✔' if i == 0 else ''}")
        lines += ["-" * 78, "Dryrun finished."]
        return self._submit("sky.launch (dryrun)", None, 1, lines)

    def list_cluster_jobs(self, cluster: str) -> list[ClusterJobRow]:
        self._check()
        with self._lock:
            c = self._clusters.get(cluster)
            if c is None:
                raise BackendError(f"cluster {cluster!r} does not exist")
            return sorted(c.jobs, key=lambda j: -j.job_id)

    def list_managed_jobs(self) -> list[ManagedJobRow]:
        self._check()
        now = self._now()
        with self._lock:
            return [
                ManagedJobRow(
                    job_id=j["job_id"], name=j["name"], status=j["status"], resources=j["resources"], infra=j["infra"],
                    submitted_at=j["submitted_at"], duration=(j["end"] or now) - j["submitted_at"],
                    recoveries=j["recoveries"], user=self.user, user_yaml=j["yaml"],
                )
                for j in sorted(self._mjobs.values(), key=lambda j: -j["job_id"])
            ]

    def list_services(self) -> list[ServiceRow]:
        self._check()
        now = self._now()
        with self._lock:
            out = []
            for s in self._services.values():
                reps = tuple(s["replicas"])
                ready = sum(1 for r in reps if r.status == "READY")
                out.append(
                    ServiceRow(
                        name=s["name"], status=s["status"], endpoint=f"http://34.1.2.3:30001" if ready else None,
                        ready_replicas=ready, total_replicas=len(reps), versions="1", uptime=s["created"] if ready else None,
                        replicas=reps, yaml=s["yaml"],
                    )
                )
            return out

    def list_volumes(self) -> list[VolumeRow]:
        self._check()
        with self._lock:
            return list(self._volumes.values())

    def list_requests(self, limit: int = 100) -> list[RequestRow]:
        self._check()
        now = self._now()
        with self._lock:
            rows = [
                RequestRow(
                    request_id=r.request_id, name=r.name, status=r.status(now), created_at=r.created_at,
                    finished_at=None if r.status(now) == "RUNNING" else r.created_at + r.duration,
                    cluster=r.cluster, user=self.user,
                )
                for r in self._requests.values()
            ]
        rows.sort(key=lambda r: -(r.created_at or 0))
        return rows[:limit]

    def list_infra(self) -> list[InfraRow]:
        self._check()
        return [InfraRow("Kubernetes/default", True, "context: default"), InfraRow("AWS", True), InfraRow("GCP", True)]

    # clusters

    def launch_cluster(self, spec: ClusterSpec) -> str:
        self._check()
        name = spec.name
        res = spec.resources
        infra = {"k8s": "Kubernetes/default", "kubernetes": "Kubernetes/default"}.get((res.infra or "k8s").split("/")[0], res.infra or "Kubernetes/default")

        def done() -> None:
            c = self._clusters.get(name)
            if c:
                c.status = "UP"
                if spec.run:
                    jid = len(c.jobs) + 1
                    c.jobs.append(ClusterJobRow(name, jid, spec.name, "PENDING", self._now(), None, None, f"{spec.num_nodes}x[{res.summary()}]", self.user))

        with self._lock:
            existing = self._clusters.get(name)
            if existing is None:
                self._clusters[name] = _Cluster(
                    name, spec.to_yaml(), infra, f"{spec.num_nodes}x {infra.split('/')[0]}({res.summary()})", self._now(),
                    autostop=spec.autostop_minutes if spec.autostop_minutes is not None else -1, to_down=spec.autostop_down,
                )
            else:
                existing.spec_yaml = spec.to_yaml()
        lines = [
            f"Considered resources ({spec.num_nodes} node):",
            "-" * 60,
            f" INFRA                 RESOURCES        COST ($)   CHOSEN",
            f" {infra:<21} {res.summary():<16} 0.00          ✔",
            "-" * 60,
            f"⚙︎ Launching on {infra}.",
            "└── Pod is up.",
            "✓ Cluster launched: " + name,
            "⚙︎ Syncing files.",
            "✓ Setup detached.",
        ]
        if spec.run:
            lines += ["⚙︎ Job submitted, ID: 1", "(task, pid=1234) hello from stratus", "✓ Job finished (status: SUCCEEDED)."]
        return self._submit("sky.launch", name, 6, lines, on_done=done)

    def exec_on_cluster(self, cluster: str, command: str) -> str:
        self._check()
        with self._lock:
            c = self._require_cluster(cluster)
            if c.status != "UP":
                raise BackendError(f"cluster {cluster} is {c.status}; start it first")
            jid = len(c.jobs) + 1
            c.jobs.append(ClusterJobRow(cluster, jid, "stratus-exec", "PENDING", self._now(), None, None, "1x[CPU:1]", self.user))
        return self._submit("sky.exec", cluster, 2, [f"⚙︎ Job submitted, ID: {jid}", f"$ {command}", "(task) done"])

    def _require_cluster(self, name: str) -> _Cluster:
        c = self._clusters.get(name)
        if c is None:
            raise BackendError(f"cluster {name!r} does not exist")
        return c

    def _set_status_later(self, name: str, req: str, status: str, lines: list[str], remove: bool = False) -> str:
        def done() -> None:
            if remove:
                self._clusters.pop(name, None)
            elif name in self._clusters:
                self._clusters[name].status = status

        return self._submit(req, name, 3, lines, on_done=done)

    def stop_cluster(self, cluster: str) -> str:
        self._check()
        self._require_cluster(cluster)
        return self._set_status_later(cluster, "sky.stop", "STOPPED", [f"Stopping cluster {cluster}...", f"Cluster {cluster} stopped."])

    def start_cluster(self, cluster: str) -> str:
        self._check()
        c = self._require_cluster(cluster)
        c.status = "INIT"
        return self._set_status_later(cluster, "sky.start", "UP", [f"Starting cluster {cluster}...", "✓ Cluster started."])

    def down_cluster(self, cluster: str) -> str:
        self._check()
        self._require_cluster(cluster)
        return self._set_status_later(cluster, "sky.down", "", [f"Terminating cluster {cluster}...", f"Cluster {cluster} terminated."], remove=True)

    def set_autostop(self, cluster: str, minutes: int, down: bool) -> str:
        self._check()
        c = self._require_cluster(cluster)
        c.autostop, c.to_down = minutes, down
        verb = "autodown" if down else "autostop"
        msg = f"{verb} cancelled" if minutes < 0 else f"{verb} set to {minutes} min"
        return self._submit("sky.autostop", cluster, 0.5, [f"Cluster {cluster}: {msg}."])

    def cancel_cluster_job(self, cluster: str, job_id: int) -> str:
        self._check()
        with self._lock:
            c = self._require_cluster(cluster)
            c.jobs = [replace(j, status="CANCELLED", end_at=self._now()) if j.job_id == job_id else j for j in c.jobs]
        return self._submit("sky.cancel", cluster, 0.5, [f"Cancelled job {job_id} on {cluster}."])

    def ssh_argv(self, cluster: str) -> list[str]:
        self._require_cluster(cluster)
        return ["sh", "-c", f"echo 'demo mode: would run  ssh {cluster}'; echo 'press enter to return'; read _"]

    # managed jobs

    def launch_managed_job(self, spec: JobSpec) -> str:
        self._check()
        if spec.pool and spec.pool not in self._pools:
            raise BackendError(f"pool {spec.pool!r} does not exist")
        jid = next(self._job_ids)
        with self._lock:
            self._mjobs[jid] = {
                "job_id": jid, "name": spec.name, "status": "PENDING", "resources": f"{spec.num_nodes}x[{spec.resources.summary()}]",
                "infra": spec.resources.infra or "-", "submitted_at": self._now(), "end": None, "recoveries": 0, "yaml": spec.to_yaml(),
            }
        return self._submit("jobs.launch", None, 2, [f"Managed job {spec.name!r} submitted (ID: {jid})."])

    def cancel_managed_job(self, job_id: int) -> str:
        self._check()
        with self._lock:
            if job_id not in self._mjobs:
                raise BackendError(f"managed job {job_id} not found")
            self._mjobs[job_id]["status"] = "CANCELLING"
            self._mjobs[job_id]["end"] = self._now()
        return self._submit("jobs.cancel", None, 0.5, [f"Cancelling managed job {job_id}."])

    # services

    def serve_up(self, spec: ServiceSpec) -> str:
        self._check()
        with self._lock:
            if spec.name in self._services:
                raise BackendError(f"service {spec.name!r} already exists; use update")
            now = self._now()
            self._services[spec.name] = {
                "name": spec.name, "status": "CONTROLLER_INIT", "created": now, "yaml": spec.to_yaml(),
                "replicas": [ReplicaRow(spec.name, i + 1, "PROVISIONING", 1, spec.resources.infra or "-", spec.resources.summary(), None, now) for i in range(max(spec.min_replicas, 1))],
            }
        return self._submit("serve.up", None, 2, [f"Service {spec.name!r} is spinning up."])

    def serve_update(self, spec: ServiceSpec) -> str:
        self._check()
        with self._lock:
            if spec.name not in self._services:
                raise BackendError(f"service {spec.name!r} does not exist")
            self._services[spec.name]["yaml"] = spec.to_yaml()
        return self._submit("serve.update", None, 1, [f"Rolling update of {spec.name!r} started."])

    def serve_down(self, name: str) -> str:
        self._check()
        with self._lock:
            if name not in self._services:
                raise BackendError(f"service {name!r} does not exist")
            self._services[name]["status"] = "SHUTTING_DOWN"
        return self._submit("serve.down", None, 2, [f"Tearing down {name}."], on_done=lambda: self._services.pop(name, None))

    def terminate_replica(self, service: str, replica_id: int) -> str:
        self._check()
        with self._lock:
            s = self._services.get(service)
            if s:
                s["replicas"] = [r for r in s["replicas"] if r.replica_id != replica_id]
        return self._submit("serve.terminate_replica", None, 0.5, [f"Replica {replica_id} terminated."])

    # volumes

    # pools & storage

    def pool_apply(self, spec: PoolSpec) -> str:
        self._check()
        now = self._now()
        with self._lock:
            self._pools[spec.name] = {
                "name": spec.name, "status": "READY", "resources": f"1x[{spec.resources.summary()}]", "yaml": spec.to_yaml(),
                "workers": [ReplicaRow(spec.name, i + 1, "READY", 1, spec.resources.infra or "-", spec.resources.summary(), None, now)
                            for i in range(spec.workers)],
            }
        return self._submit("jobs.pool_apply", None, 2, [f"Pool {spec.name!r} applied with {spec.workers} worker(s)."])

    def pool_down(self, name: str) -> str:
        self._check()
        with self._lock:
            if name not in self._pools:
                raise BackendError(f"pool {name!r} does not exist")
            self._pools[name]["status"] = "SHUTTING_DOWN"
        return self._submit("jobs.pool_down", None, 1, [f"Tearing down pool {name}."], on_done=lambda: self._pools.pop(name, None))

    def delete_storage(self, name: str) -> str:
        self._check()
        with self._lock:
            if self._storage.pop(name, None) is None:
                raise BackendError(f"storage {name!r} not found")
        return self._submit("storage_delete", None, 0.5, [f"Deleted storage {name}."])

    def stream_pool_logs(self, pool: str, target: str, worker_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        who = f"worker {worker_id}" if worker_id else target
        for i in range(20):
            if stop.wait(self._t(0.3)):
                return
            sink(f"[{pool}/{who}] heartbeat {i}: idle, waiting for jobs")

    def apply_volume(self, spec: VolumeSpec) -> str:
        self._check()
        with self._lock:
            self._volumes[spec.name] = VolumeRow(
                spec.name, spec.type, spec.infra or "-", spec.size, "READY", (), self._now(), self.user, creation_yaml=spec.to_yaml()
            )
        return self._submit("volumes.apply", None, 1, [f"Volume {spec.name} created."])

    def delete_volume(self, name: str) -> str:
        self._check()
        with self._lock:
            vol = self._volumes.get(name)
            if vol is None:
                raise BackendError(f"volume {name!r} not found")
            if vol.used_by:
                raise BackendError(f"volume {name} is in use by {', '.join(vol.used_by)}")
            self._volumes.pop(name)
        return self._submit("volumes.delete", None, 0.5, [f"Volume {name} deleted."])

    # requests & logs

    def cancel_request(self, request_id: str) -> None:
        with self._lock:
            if request_id in self._requests:
                self._requests[request_id].cancelled = True

    def stream_request_logs(self, request_id: str, sink: LogSink, stop: threading.Event) -> None:
        with self._lock:
            req = self._requests.get(request_id)
        if req is None:
            sink(f"[stratus] request {request_id} not found")
            return
        step = req.duration / max(len(req.lines), 1)
        for line in req.lines:
            if stop.wait(step):
                return
            sink(line)
        self._tick()
        if req.fail:
            sink(f"[stratus] request failed: {req.fail}")

    def _synthetic_logs(self, prefix: str, sink: LogSink, stop: threading.Event, count: int = 40) -> None:
        for i in range(count):
            if stop.wait(self._t(0.25)):
                return
            sink(f"({prefix}) step {i:>4} | loss {2.5 / (i + 1):.4f} | lr 3e-4")

    def stream_cluster_job_logs(self, cluster: str, job_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        sink(f"Tailing logs of job {job_id or 'latest'} on cluster {cluster!r}...")
        self._synthetic_logs("task, pid=4242", sink, stop)

    def stream_managed_job_logs(self, job_id: int, sink: LogSink, stop: threading.Event, controller: bool = False, follow: bool = True) -> None:
        if controller:
            sink(f"[controller] managing job {job_id}: launching task cluster")
            sink("[controller] task cluster is up; job RUNNING")
            return
        self._synthetic_logs(f"job-{job_id}", sink, stop)

    def stream_service_logs(self, service: str, target: str, replica_id: int | None, sink: LogSink, stop: threading.Event) -> None:
        for i in range(30):
            if stop.wait(self._t(0.3)):
                return
            who = f"replica {replica_id}" if target == "replica" else target
            sink(f"[{service}/{who}] GET /v1/completions 200 {12 + i % 7}ms")
