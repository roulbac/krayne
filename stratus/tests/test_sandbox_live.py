"""Live checks against a running `stratus sandbox` (or any API server with k8s).

Run with:  uv run pytest -m sandbox
Uses $STRATUS_TEST_ENDPOINT (default http://127.0.0.1:46580).
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from stratus.backend.skypilot import SkyPilotBackend
from stratus.specs.models import ClusterSpec, JobSpec, Resources, VolumeSpec

pytestmark = [pytest.mark.sandbox, pytest.mark.timeout(1200)]

ENDPOINT = os.environ.get("STRATUS_TEST_ENDPOINT", "http://127.0.0.1:46580")


@pytest.fixture(scope="module")
def backend():
    b = SkyPilotBackend(ENDPOINT)
    if not b.server_info().healthy:
        pytest.skip(f"no healthy SkyPilot API server at {ENDPOINT}")
    return b


def _suffix() -> str:
    return uuid.uuid4().hex[:5]


def _drain(backend, request_id, timeout=900) -> list[str]:
    lines: list[str] = []
    stop = threading.Event()
    t = threading.Thread(target=backend.stream_request_logs, args=(request_id, lines.append, stop), daemon=True)
    t.start()
    t.join(timeout)
    stop.set()
    return lines


def _wait(pred, timeout=600, every=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(every)
    return False


def test_listings(backend):
    backend.list_clusters()
    backend.list_managed_jobs()
    backend.list_services()
    backend.list_volumes()
    assert backend.list_requests() is not None
    infra = [r.name.lower() for r in backend.list_infra()]
    assert any(i.startswith("kubernetes") for i in infra)


def test_volume_roundtrip(backend):
    name = f"t-vol-{_suffix()}"
    _drain(backend, backend.apply_volume(VolumeSpec(name=name, size="1Gi")), 120)
    assert _wait(lambda: name in [v.name for v in backend.list_volumes()], 60, 2)
    _drain(backend, backend.delete_volume(name), 120)
    assert _wait(lambda: name not in [v.name for v in backend.list_volumes()], 60, 2)


def test_cluster_launch_exec_logs_down(backend):
    name = f"t-c-{_suffix()}"
    spec = ClusterSpec(name=name, resources=Resources(infra="k8s", cpus="1", memory="1"), run="echo stratus-live-ok")
    lines = _drain(backend, backend.launch_cluster(spec))
    try:
        assert any("Cluster launched" in l or "Job submitted" in l for l in lines), lines[-20:]
        assert _wait(lambda: any(c.name == name and c.status == "UP" for c in backend.list_clusters()), 120, 3)
        assert _wait(lambda: any(j.status == "SUCCEEDED" for j in backend.list_cluster_jobs(name)), 120, 3)

        _drain(backend, backend.exec_on_cluster(name, "echo exec-ok"), 300)
        out: list[str] = []
        stop = threading.Event()
        backend.stream_cluster_job_logs(name, None, out.append, stop)
        assert any("exec-ok" in l for l in out), out[-10:]
    finally:
        _drain(backend, backend.down_cluster(name), 300)
    assert _wait(lambda: name not in [c.name for c in backend.list_clusters()], 120, 3)


def test_managed_job(backend):
    name = f"t-j-{_suffix()}"
    spec = JobSpec(name=name, resources=Resources(infra="k8s", cpus="1", memory="1"), run="echo managed-ok")
    _drain(backend, backend.launch_managed_job(spec), 300)

    def done():
        jobs = [j for j in backend.list_managed_jobs() if j.name == name]
        return bool(jobs) and jobs[0].status in ("SUCCEEDED", "FAILED", "FAILED_SETUP", "CANCELLED")

    assert _wait(done, 900, 5)
    job = next(j for j in backend.list_managed_jobs() if j.name == name)
    assert job.status == "SUCCEEDED"
    out: list[str] = []
    backend.stream_managed_job_logs(job.job_id, out.append, threading.Event(), follow=False)
    assert any("managed-ok" in l for l in out), out[-10:]
