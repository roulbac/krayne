"""Per-cluster tunnel manager subprocess.

Launched detached by :func:`krayne.tunnel.start_tunnels` as
``python -m krayne._manager <cluster> <namespace>``. Reads desired tunnels
from the cluster's state file, opens in-process port-forwards via the
Kubernetes Python client, and self-heals broken forwards. Exits when the
desired-tunnel set has been empty for ``IDLE_EXIT_SECONDS``.

Concurrency: a single ``anyio`` event loop on the asyncio backend, with
one task group holding the reconcile-tick, heartbeat, head-pod watch, and
per-service Forwarder tasks. All blocking Kubernetes-client calls run via
``anyio.to_thread.run_sync(..., abandon_on_cancel=True)``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import socket
import sys
import time
from contextlib import suppress
from typing import Any

import anyio
import anyio.abc
import psutil

from krayne import tunnel_state
from krayne.tunnel_state import (
    KubeConfigRef,
    ManagerInfo,
    ServiceState,
    ServiceStatus,
    TunnelInfo,
    TunnelState,
)

RECONCILE_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 1.0
IDLE_EXIT_SECONDS = 5.0
RESTART_WINDOW_SECONDS = 300.0
RESTART_BUDGET = 5
WATCH_RECONNECT_DELAY = 2.0
LOG_MAX_BYTES = 1 * 1024 * 1024  # 1 MiB
LOG_BACKUP_COUNT = 2

log = logging.getLogger("krayne.manager")


def main() -> None:
    """Entry point invoked as ``python -m krayne._manager <cluster> <ns>``."""
    if len(sys.argv) < 3:
        print("usage: python -m krayne._manager <cluster> <namespace>",
              file=sys.stderr)
        sys.exit(2)
    cluster, namespace = sys.argv[1], sys.argv[2]

    _configure_logging(cluster, namespace)
    log.info("manager starting pid=%d cluster=%s namespace=%s",
             os.getpid(), cluster, namespace)

    try:
        _claim_manager_slot(cluster, namespace)
    except _ManagerAlreadyClaimedError as exc:
        log.warning("another manager is alive (%s); exiting", exc)
        sys.exit(0)

    try:
        anyio.run(Manager(cluster, namespace).run, backend="asyncio")
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("manager crashed")
        raise
    finally:
        _release_manager_slot(cluster, namespace)
        log.info("manager exited")


def _configure_logging(cluster: str, namespace: str) -> None:
    log_path = tunnel_state.log_path(cluster, namespace)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


class _ManagerAlreadyClaimedError(RuntimeError):
    pass


def _claim_manager_slot(cluster: str, namespace: str) -> None:
    """Write our PID + start_time into the state file under the lock.

    If a live manager (heartbeat fresh, fingerprint matches) is already
    recorded, raise so the caller can exit without disrupting it.
    """
    pid = os.getpid()
    start_time = psutil.Process(pid).create_time()

    def _claim(state: TunnelState) -> TunnelState:
        if state.manager is not None and tunnel_state.manager_alive(state.manager):
            raise _ManagerAlreadyClaimedError(
                f"pid={state.manager.pid} heartbeat={state.manager.heartbeat}"
            )
        state.manager = ManagerInfo(
            pid=pid, start_time=start_time, heartbeat=time.time(),
        )
        return state

    tunnel_state.update(cluster, namespace, _claim)


def _release_manager_slot(cluster: str, namespace: str) -> None:
    pid = os.getpid()

    def _release(state: TunnelState) -> TunnelState:
        if state.manager is not None and state.manager.pid == pid:
            state.manager = None
        # Clear all per-service status entries — they are observed state
        # only meaningful while the manager owns them.
        state.status = {}
        return state

    with suppress(Exception):
        tunnel_state.update(cluster, namespace, _release)


class Manager:
    """Reconcile observed Forwarders against the desired-state file."""

    def __init__(self, cluster: str, namespace: str) -> None:
        self.cluster = cluster
        self.namespace = namespace
        self.forwarders: dict[str, Forwarder] = {}
        self._idle_since: float | None = None
        self._kube = None  # lazily constructed once we read kube_config
        self._tg: anyio.abc.TaskGroup | None = None

    async def run(self) -> None:
        async with anyio.create_task_group() as tg:
            self._tg = tg
            tg.start_soon(self._signal_loop)
            tg.start_soon(self._heartbeat_loop)
            tg.start_soon(self._reconcile_loop)
            tg.start_soon(self._head_pod_watch_loop)

    async def _signal_loop(self) -> None:
        assert self._tg is not None
        with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
            async for _sig in signals:
                log.info("signal received; cancelling")
                self._tg.cancel_scope.cancel()
                return

    async def _heartbeat_loop(self) -> None:
        while True:
            await anyio.sleep(HEARTBEAT_INTERVAL)
            try:
                await anyio.to_thread.run_sync(
                    self._bump_heartbeat, abandon_on_cancel=True,
                )
            except Exception:
                log.exception("heartbeat write failed")

    def _bump_heartbeat(self) -> None:
        def _tick(state: TunnelState) -> TunnelState:
            if state.manager is not None:
                state.manager.heartbeat = time.time()
            return state

        tunnel_state.update(self.cluster, self.namespace, _tick)

    async def _reconcile_loop(self) -> None:
        assert self._tg is not None
        while True:
            try:
                await self._reconcile_once()
            except Exception:
                log.exception("reconcile tick failed")
            await anyio.sleep(RECONCILE_INTERVAL)

    async def _reconcile_once(self) -> None:
        assert self._tg is not None
        state = await anyio.to_thread.run_sync(
            tunnel_state.load_state, self.cluster, self.namespace,
            abandon_on_cancel=True,
        )
        if state is None:
            log.warning("state file disappeared; exiting")
            self._tg.cancel_scope.cancel()
            return

        if self._kube is None:
            self._kube = _build_kube_client(state.kube_config)

        desired = {t.service: t for t in state.desired_tunnels}

        # Stop forwarders no longer desired.
        for svc in list(self.forwarders):
            if svc not in desired:
                fwd = self.forwarders.pop(svc)
                fwd.cancel()
                log.info("stopped forwarder for %s", svc)

        # Start forwarders for new desired entries.
        for svc, info in desired.items():
            if svc not in self.forwarders:
                fwd = Forwarder(
                    cluster=self.cluster,
                    namespace=self.namespace,
                    info=info,
                    kube=self._kube,
                )
                self.forwarders[svc] = fwd
                self._tg.start_soon(fwd.run)
                log.info("started forwarder for %s -> :%d",
                         svc, info.local_port)

        # Publish status diff.
        await anyio.to_thread.run_sync(
            self._publish_status, abandon_on_cancel=True,
        )

        # Idle-exit bookkeeping.
        if not desired:
            if self._idle_since is None:
                self._idle_since = time.monotonic()
            elif time.monotonic() - self._idle_since >= IDLE_EXIT_SECONDS:
                log.info("idle for %.0fs; exiting", IDLE_EXIT_SECONDS)
                self._tg.cancel_scope.cancel()
        else:
            self._idle_since = None

    def _publish_status(self) -> None:
        snapshot = {svc: fwd.snapshot() for svc, fwd in self.forwarders.items()}

        def _apply(state: TunnelState) -> TunnelState:
            state.status = snapshot
            return state

        tunnel_state.update(self.cluster, self.namespace, _apply)

    async def _head_pod_watch_loop(self) -> None:
        """Mark forwarders broken when the head pod is replaced.

        Reconnects the watch stream after errors with a small backoff;
        the reconcile loop is the canonical recovery path so we only need
        to nudge it.
        """
        while True:
            try:
                if self._kube is None:
                    await anyio.sleep(WATCH_RECONNECT_DELAY)
                    continue
                await anyio.to_thread.run_sync(
                    self._run_watch_once, abandon_on_cancel=True,
                )
            except Exception:
                log.exception("head-pod watch failed; backing off")
            await anyio.sleep(WATCH_RECONNECT_DELAY)

    def _run_watch_once(self) -> None:
        assert self._kube is not None
        for evt in self._kube.watch_pods(
            self.cluster, self.namespace, timeout_seconds=60,
        ):
            etype = evt.get("type")
            pod = evt.get("object")
            if pod is None:
                continue
            labels = getattr(pod.metadata, "labels", None) or {}
            if labels.get("ray.io/node-type") != "head":
                continue
            if etype in ("DELETED", "MODIFIED"):
                for fwd in self.forwarders.values():
                    fwd.mark_pod_changed(pod.metadata.name)


class Forwarder:
    """Per-service in-process port-forward.

    Binds ``127.0.0.1:<local_port>`` once; on every accept, opens a fresh
    ``kubernetes.stream.portforward`` to the current head pod and bridges
    bytes between the local socket and the pod-side socket via two worker
    threads (one per direction) using ``abandon_on_cancel=True``.
    """

    def __init__(
        self,
        *,
        cluster: str,
        namespace: str,
        info: TunnelInfo,
        kube: Any,
    ) -> None:
        self.cluster = cluster
        self.namespace = namespace
        self.info = info
        self.kube = kube
        self._cancel_scope: anyio.CancelScope | None = None
        self._state = ServiceState.PENDING
        self._pod: str | None = None
        self._restart_count = 0
        self._last_restart: float | None = None
        self._last_error: str | None = None
        self._restart_window_start: float = time.monotonic()

    def snapshot(self) -> ServiceStatus:
        return ServiceStatus(
            state=self._state,
            pod=self._pod,
            restart_count=self._restart_count,
            last_restart=self._last_restart,
            last_error=self._last_error,
        )

    def cancel(self) -> None:
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()

    def mark_pod_changed(self, new_pod: str) -> None:
        """Called by the head-pod watch when the head pod is replaced."""
        if self._pod is not None and self._pod != new_pod:
            self._state = ServiceState.BROKEN
            self._last_error = f"head pod replaced ({self._pod} -> {new_pod})"
            self._pod = new_pod

    async def run(self) -> None:
        with anyio.CancelScope() as scope:
            self._cancel_scope = scope
            while True:
                if self._state == ServiceState.FAILED:
                    await anyio.sleep(RECONCILE_INTERVAL)
                    continue
                try:
                    await self._serve_once()
                except anyio.get_cancelled_exc_class():
                    raise
                except Exception as exc:
                    log.exception("forwarder %s loop failed", self.info.service)
                    self._record_failure(str(exc))

    async def _serve_once(self) -> None:
        try:
            listener = await anyio.create_tcp_listener(
                local_host="127.0.0.1",
                local_port=self.info.local_port,
            )
        except OSError as exc:
            self._record_failure(f"bind {self.info.local_port}: {exc}")
            await anyio.sleep(RECONCILE_INTERVAL)
            return

        self._state = ServiceState.OPEN
        async with listener:
            try:
                await listener.serve(self._handle_connection)
            finally:
                # If serve returns without an exception, we mark broken so
                # the outer loop re-binds on the next iteration.
                if self._state != ServiceState.FAILED:
                    self._state = ServiceState.BROKEN

    async def _handle_connection(self, client: anyio.abc.SocketStream) -> None:
        ws: Any | None = None
        sock: socket.socket | None = None
        try:
            pod = await anyio.to_thread.run_sync(
                self._resolve_head_pod, abandon_on_cancel=True,
            )
            if pod is None:
                self._last_error = "no Running head pod"
                self._state = ServiceState.BROKEN
                await client.aclose()
                return

            self._pod = pod
            ws = await anyio.to_thread.run_sync(
                self._open_portforward, pod, abandon_on_cancel=True,
            )
            sock = ws.socket(self.info.remote_port)
            try:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._pump_client_to_pod, client, sock)
                    tg.start_soon(self._pump_pod_to_client, client, sock)
            finally:
                # Defensive shutdown: ensure the blocking pump threads
                # return from their next recv/sendall.
                with suppress(OSError):
                    sock.shutdown(socket.SHUT_RDWR)
        except anyio.get_cancelled_exc_class():
            raise
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("forwarder %s connection error: %s",
                        self.info.service, exc)
        finally:
            if sock is not None:
                with suppress(Exception):
                    sock.close()
            if ws is not None:
                with suppress(Exception):
                    ws.close()
            with suppress(Exception):
                await client.aclose()

    def _resolve_head_pod(self) -> str | None:
        return self.kube.head_pod_name(self.cluster, self.namespace)

    def _open_portforward(self, pod: str) -> Any:
        return self.kube.portforward(
            pod, self.namespace, [self.info.remote_port],
        )

    async def _pump_client_to_pod(
        self, client: anyio.abc.SocketStream, sock: socket.socket,
    ) -> None:
        while True:
            try:
                data = await client.receive(65536)
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                with suppress(OSError):
                    await anyio.to_thread.run_sync(
                        sock.shutdown, socket.SHUT_WR,
                        abandon_on_cancel=True,
                    )
                return
            if not data:
                with suppress(OSError):
                    await anyio.to_thread.run_sync(
                        sock.shutdown, socket.SHUT_WR,
                        abandon_on_cancel=True,
                    )
                return
            await anyio.to_thread.run_sync(
                sock.sendall, data, abandon_on_cancel=True,
            )

    async def _pump_pod_to_client(
        self, client: anyio.abc.SocketStream, sock: socket.socket,
    ) -> None:
        while True:
            data = await anyio.to_thread.run_sync(
                sock.recv, 65536, abandon_on_cancel=True,
            )
            if not data:
                with suppress(Exception):
                    await client.send_eof()
                return
            try:
                await client.send(data)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                return

    def _record_failure(self, message: str) -> None:
        now = time.monotonic()
        if now - self._restart_window_start > RESTART_WINDOW_SECONDS:
            self._restart_window_start = now
            self._restart_count = 0
        self._restart_count += 1
        self._last_restart = time.time()
        self._last_error = message
        if self._restart_count > RESTART_BUDGET:
            log.error("forwarder %s exceeded restart budget; marking FAILED",
                      self.info.service)
            self._state = ServiceState.FAILED
        else:
            self._state = ServiceState.BROKEN


def _build_kube_client(kube_config: KubeConfigRef) -> Any:
    """Construct a KubeClient from the persisted kube_config ref.

    Imported lazily so the manager doesn't pay the kubernetes-client
    import cost until actually needed.
    """
    from krayne.kube.client import get_kube_client

    return get_kube_client(
        kubeconfig=kube_config.kubeconfig,
        context=kube_config.context,
    )


if __name__ == "__main__":
    main()
