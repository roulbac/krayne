from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from krayne.cli import app as _state
from krayne.errors import KrayneError


@_state.app.command(
    "submit",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def submit(
    ctx: typer.Context,
    cluster: str = typer.Option(..., "--cluster", "-c", help="Target cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace", help="Kubernetes namespace."),
    working_dir: Path | None = typer.Option(
        None,
        "--working-dir",
        help="Directory uploaded to the cluster (defaults to the current directory).",
    ),
    no_wait: bool = typer.Option(
        False,
        "--no-wait",
        help="Submit and return immediately instead of tailing job logs to completion.",
    ),
) -> None:
    """Submit a Ray job to a remote cluster.

    Mirrors ``ray job submit`` argv: everything after ``--`` is the entrypoint
    command executed on the cluster's head pod, so the caller picks the
    interpreter (``python``, ``uv run``, ``bash``, ...). Opens a dashboard
    tunnel if one isn't already up.

    Examples::

        krayne submit --cluster foo -- python train.py --epochs 10
        krayne submit --cluster foo -- uv run --extra demo demo_serve.py
        krayne submit --cluster foo --no-wait -- bash entrypoint.sh
    """
    from krayne.tunnel import (
        is_tunnel_active,
        load_tunnel_state,
        start_tunnels,
        wait_for_tunnel_ready,
    )

    try:
        entrypoint = list(ctx.args)
        if not entrypoint:
            raise KrayneError(
                "Missing entrypoint. Pass the command to run on the cluster "
                "after `--`, e.g. `krayne submit --cluster foo -- python train.py`."
            )

        wd = (working_dir or Path.cwd()).expanduser().resolve()
        if not wd.is_dir():
            raise KrayneError(f"Working directory not found: {wd}")

        info = _state._get_cluster(cluster, namespace, kubeconfig=_state._kubeconfig)
        if info.status not in ("ready", "running"):
            raise KrayneError(
                f"Cluster '{cluster}' is not ready (status: {info.status})."
            )

        if not is_tunnel_active(cluster, namespace):
            services = _state._get_cluster_services(
                cluster, namespace, kubeconfig=_state._kubeconfig
            )
            if "dashboard" not in services:
                raise KrayneError(
                    f"Cluster '{cluster}' does not expose a dashboard service; "
                    "cannot submit jobs."
                )
            _state.console.print(
                f"Opening tunnel to '{cluster}'…", style="dim"
            )
            start_tunnels(cluster, namespace, services, kubeconfig=_state._kubeconfig)

        state = load_tunnel_state(cluster, namespace)
        if state is None:
            raise KrayneError("Tunnel state unavailable after start; cannot continue.")
        dashboard = next(
            (t for t in state.tunnels if t.service == "dashboard"),
            None,
        )
        if dashboard is None:
            raise KrayneError(
                "Dashboard tunnel not found. Check `krayne tun-open` separately."
            )

        # start_tunnels already blocks on the manager's status, but probe
        # the TCP port one more time as a final readiness check in case the
        # listener reports OPEN before its accept() loop is actually ready.
        if not wait_for_tunnel_ready(dashboard, timeout=30.0):
            raise KrayneError(
                f"Dashboard tunnel for '{cluster}' did not become reachable at "
                f"{dashboard.local_url} within 30s. Try `krayne tun-close {cluster}` "
                "and re-run."
            )
        dashboard_url = dashboard.local_url

        ray_cli = shutil.which("ray")
        if ray_cli is None:
            raise KrayneError(
                "The 'ray' CLI is not on PATH. Install ray in this environment first."
            )

        cmd = [
            ray_cli, "job", "submit",
            "--address", dashboard_url,
            "--working-dir", str(wd),
        ]
        if no_wait:
            cmd.append("--no-wait")
        cmd += ["--", *entrypoint]

        _state.console.print(
            f"Submitting [bold]{' '.join(entrypoint)}[/bold] to "
            f"[bold]{cluster}[/bold] via {dashboard_url}",
            style="dim",
        )
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
    except KrayneError as exc:
        _state._handle_error(exc)
