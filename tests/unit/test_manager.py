"""Component tests for the per-cluster tunnel manager (``krayne._manager``).

These replace the flaky end-to-end ``test_tunnel_survives_head_pod_restart``
integration test. That test killed a real head pod and waited for KubeRay to
reschedule a replacement under memory pressure on the 6 GiB sandbox node — a
multi-stage, timing-sensitive recovery whose flakiness lived almost entirely
in KubeRay's scheduler and Ray's dashboard cold-start, *not* in krayne code.

The krayne-side regression the e2e protected is small and fully deterministic:

  1. The head-pod watch notices the head pod was replaced and marks every
     forwarder BROKEN, updating its target pod (``Forwarder.mark_pod_changed``,
     driven by ``Manager._run_watch_once``).
  2. The forwarder resolves the head pod *live* on each (re)connect rather than
     caching it, so the next connection targets the new pod
     (``Forwarder._resolve_head_pod`` / ``_open_portforward``, called per
     connection from ``_handle_connection``).

Together those reconstitute "manager re-resolves the new pod and re-binds
without user intervention". The real port-forward byte path is already covered
end-to-end by ``tests/integration``'s ``test_dashboard_reachable_via_tunnel``.
"""

from __future__ import annotations

from types import SimpleNamespace

from krayne._manager import Forwarder, Manager
from krayne.tunnel_state import ServiceState, TunnelInfo


def _make_forwarder(kube=None, *, service: str = "dashboard") -> Forwarder:
    return Forwarder(
        cluster="c",
        namespace="ns",
        info=TunnelInfo(
            service=service,
            remote_port=8265,
            local_port=12345,
            local_url="http://localhost:12345",
        ),
        kube=kube,
    )


def _head_pod_event(event_type: str, name: str, *, node_type: str = "head") -> dict:
    """Build a watch event shaped like ``KubeClient.watch_pods`` yields."""
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels={"ray.io/node-type": node_type})
    )
    return {"type": event_type, "object": pod}


class FakeKube:
    """Minimal KubeClient stand-in for the forwarder's head-pod resolution."""

    def __init__(self, head_pod: str | None = None, events: list[dict] | None = None):
        self.head_pod = head_pod
        self._events = events or []
        self.portforward_calls: list[str] = []

    def head_pod_name(self, cluster_name: str, namespace: str) -> str | None:
        return self.head_pod

    def watch_pods(self, cluster_name: str, namespace: str, *, timeout_seconds=None):
        return iter(self._events)

    def portforward(self, pod_name: str, namespace: str, ports: list[int]):
        self.portforward_calls.append(pod_name)
        return SimpleNamespace(socket=lambda port: None, close=lambda: None)


class TestMarkPodChanged:
    def test_marks_broken_and_updates_pod(self):
        fwd = _make_forwarder()
        fwd._pod = "old-head"
        fwd._state = ServiceState.OPEN

        fwd.mark_pod_changed("new-head")

        assert fwd._state == ServiceState.BROKEN
        assert fwd._pod == "new-head"
        assert "old-head" in (fwd._last_error or "")
        assert "new-head" in (fwd._last_error or "")

    def test_noop_when_pod_unchanged(self):
        fwd = _make_forwarder()
        fwd._pod = "head-1"
        fwd._state = ServiceState.OPEN

        fwd.mark_pod_changed("head-1")

        assert fwd._state == ServiceState.OPEN
        assert fwd._pod == "head-1"

    def test_noop_before_first_pod_resolved(self):
        # Nothing to reconnect from until a pod has been bound at least once.
        fwd = _make_forwarder()
        assert fwd._pod is None

        fwd.mark_pod_changed("first-head")

        assert fwd._state == ServiceState.PENDING
        assert fwd._pod is None


class TestWatchPropagation:
    def test_head_pod_replacement_marks_all_forwarders_broken(self):
        manager = Manager("c", "ns")
        fwd_a = _make_forwarder(service="dashboard")
        fwd_a._pod = "old-head"
        fwd_a._state = ServiceState.OPEN
        fwd_b = _make_forwarder(service="notebook")
        fwd_b._pod = "old-head"
        fwd_b._state = ServiceState.OPEN
        manager.forwarders = {"dashboard": fwd_a, "notebook": fwd_b}
        manager._kube = FakeKube(events=[_head_pod_event("MODIFIED", "new-head")])

        manager._run_watch_once()

        for fwd in (fwd_a, fwd_b):
            assert fwd._state == ServiceState.BROKEN
            assert fwd._pod == "new-head"

    def test_deleted_event_also_triggers_reconnect(self):
        manager = Manager("c", "ns")
        fwd = _make_forwarder()
        fwd._pod = "old-head"
        fwd._state = ServiceState.OPEN
        manager.forwarders = {"dashboard": fwd}
        manager._kube = FakeKube(events=[_head_pod_event("DELETED", "new-head")])

        manager._run_watch_once()

        assert fwd._state == ServiceState.BROKEN

    def test_ignores_non_head_and_non_lifecycle_events(self):
        manager = Manager("c", "ns")
        fwd = _make_forwarder()
        fwd._pod = "old-head"
        fwd._state = ServiceState.OPEN
        manager.forwarders = {"dashboard": fwd}
        manager._kube = FakeKube(
            events=[
                _head_pod_event("MODIFIED", "worker-pod", node_type="worker"),
                _head_pod_event("ADDED", "new-head"),
            ]
        )

        manager._run_watch_once()

        # Worker churn and ADDED events must not disturb the head forwarder.
        assert fwd._state == ServiceState.OPEN
        assert fwd._pod == "old-head"


class TestLiveHeadPodResolution:
    def test_resolves_current_head_pod_each_call(self):
        # The forwarder must not cache the pod: each connection re-resolves,
        # so a replaced head pod is picked up transparently on reconnect.
        kube = FakeKube(head_pod="head-1")
        fwd = _make_forwarder(kube=kube)

        assert fwd._resolve_head_pod() == "head-1"

        kube.head_pod = "head-2"
        assert fwd._resolve_head_pod() == "head-2"

    def test_portforward_targets_resolved_pod(self):
        kube = FakeKube(head_pod="head-2")
        fwd = _make_forwarder(kube=kube)

        fwd._open_portforward(fwd._resolve_head_pod())

        assert kube.portforward_calls == ["head-2"]
