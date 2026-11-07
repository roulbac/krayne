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
