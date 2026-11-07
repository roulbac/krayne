import threading
import time

import pytest

from stratus.backend.base import BackendError, ServerUnavailableError
from stratus.backend.fake import FakeBackend
from stratus.specs.models import ClusterSpec, JobSpec, Resources, ServiceSpec, VolumeSpec


def wait_for(pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_cluster_lifecycle():
    b = FakeBackend(speed=50, seed=False)
    rid = b.launch_cluster(ClusterSpec(name="c1", resources=Resources(cpus="1"), run="echo hi"))
    assert b.list_clusters()[0].status == "INIT"
    lines = []
    b.stream_request_logs(rid, lines.append, threading.Event())
    assert any("Cluster launched" in l for l in lines)
    assert wait_for(lambda: b.list_clusters()[0].status == "UP")
    assert wait_for(lambda: b.list_cluster_jobs("c1") and b.list_cluster_jobs("c1")[0].status in ("RUNNING", "SUCCEEDED"))
    b.stop_cluster("c1")
    assert wait_for(lambda: b.list_clusters()[0].status == "STOPPED")
    with pytest.raises(BackendError):
        b.exec_on_cluster("c1", "ls")
    b.down_cluster("c1")
    assert wait_for(lambda: b.list_clusters() == [])


def test_requests_listed_and_cancellable():
    b = FakeBackend(speed=1, seed=False)
    rid = b.launch_cluster(ClusterSpec(name="c1"))
    (req,) = b.list_requests()
    assert req.request_id == rid and req.status == "RUNNING"
    b.cancel_request(rid)
    assert b.list_requests()[0].status == "CANCELLED"


def test_managed_jobs_services_volumes():
    b = FakeBackend(speed=50, seed=False)
    b.launch_managed_job(JobSpec(name="j", run="x"))
    assert wait_for(lambda: b.list_managed_jobs()[0].status == "SUCCEEDED")
    b.serve_up(ServiceSpec(name="s", run="x", resources=Resources(ports=["8080"]), min_replicas=2))
    assert wait_for(lambda: b.list_services()[0].ready_replicas == 2)
    with pytest.raises(BackendError):
        b.serve_up(ServiceSpec(name="s", run="x", resources=Resources(ports=["8080"])))
    b.apply_volume(VolumeSpec(name="v"))
    assert b.list_volumes()[0].name == "v"
    b.delete_volume("v")
    assert b.list_volumes() == []


def test_unhealthy_server_raises():
    b = FakeBackend(seed=False)
    b.healthy = False
    assert not b.server_info().healthy
    with pytest.raises(ServerUnavailableError):
        b.list_clusters()


def test_stop_event_interrupts_streams():
    b = FakeBackend(speed=1)
    stop = threading.Event()
    lines = []
    t = threading.Thread(target=b.stream_cluster_job_logs, args=("dev-gpu", None, lines.append, stop))
    t.start()
    time.sleep(0.3)
    stop.set()
    t.join(2)
    assert not t.is_alive()


def test_scope_toggle_hides_other_users():
    b = FakeBackend()
    mine = {c.name for c in b.list_clusters()}
    b.all_users = True
    everyone = {c.name for c in b.list_clusters()}
    assert "alice-train" in everyone - mine


def test_costs_mark_kubernetes_unpriced_and_filter_by_window():
    b = FakeBackend()
    rows = {r.name: r for r in b.list_costs(days=None)}
    assert not rows["dev-gpu"].priced and rows["dev-gpu"].total is None
    assert rows["dev-gpu"].gpu_hours > 0  # usage still counted
    assert rows["sweep-a100"].priced and rows["sweep-a100"].total > 100
    assert "sweep-a100" not in {r.name for r in b.list_costs(days=1)}


def test_pools_storage_workspaces_preview():
    b = FakeBackend(speed=50, seed=False)
    from stratus.specs.models import PoolSpec

    b.pool_apply(PoolSpec(name="p", workers=2))
    assert b.list_pools()[0].total_workers == 2
    with pytest.raises(BackendError):
        b.launch_managed_job(JobSpec(name="j", run="x", pool="nope"))
    b.launch_managed_job(JobSpec(name="j", run="x", pool="p"))
    b.pool_down("p")
    assert wait_for(lambda: b.list_pools() == [])
    b2 = FakeBackend()
    b2.delete_storage("ckpt-bucket")
    assert "ckpt-bucket" not in [s.name for s in b2.list_storage()]
    b2.set_workspace("research")
    assert [w.name for w in b2.list_workspaces() if w.active] == ["research"]
    with pytest.raises(BackendError):
        b2.set_workspace("nope")
    lines = []
    b2.stream_request_logs(b2.preview_launch(ClusterSpec(name="x", resources=Resources(cpus="1"))), lines.append, threading.Event())
    assert any("CHOSEN" in l for l in lines)
