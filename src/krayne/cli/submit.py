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
    script: Path = typer.Argument(..., help="Python script to submit to the cluster."),
    cluster: str = typer.Option(..., "--cluster", "-c", help="Target cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace", help="Kubernetes namespace."),
    working_dir: Path | None = typer.Option(
        None,
        "--working-dir",
        help="Directory uploaded to the cluster (defaults to the script's parent).",
    ),
    no_wait: bool = typer.Option(
        False,
        "--no-wait",
        help="Submit and return immediately instead of tailing job logs to completion.",
    ),
) -> None:
    """Submit a Python script as a Ray job to a remote cluster.

    Wraps ``ray job submit``: ensures a dashboard tunnel is open (opening one if
    needed), then runs the job entrypoint against ``http://localhost:<port>``.
    Extra positional arguments after the script are forwarded to it, e.g.::

        krayne submit train.py --cluster foo -- --epochs 10
    """
    from krayne.tunnel import is_tunnel_active, load_tunnel_state, start_tunnels

    try:
        script_path = script.expanduser().resolve()
        if not script_path.is_file():
            raise KrayneError(f"Script not found: {script_path}")

        wd = (working_dir or script_path.parent).expanduser().resolve()
        if not wd.is_dir():
            raise KrayneError(f"Working directory not found: {wd}")

        try:
            rel = script_path.relative_to(wd)
        except ValueError as exc:
            raise KrayneError(
                f"Script {script_path} is not inside working directory {wd}. "
                "Pass --working-dir to point at the right parent."
            ) from exc

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
        dashboard_url = next(
            (t.local_url for t in state.tunnels if t.service == "dashboard"),
            None,
        )
        if dashboard_url is None:
            raise KrayneError(
                "Dashboard tunnel not found. Check `krayne tun-open` separately."
            )

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
        cmd += ["--", "python", str(rel), *ctx.args]

        _state.console.print(
            f"Submitting [bold]{rel}[/bold] to [bold]{cluster}[/bold] via {dashboard_url}",
            style="dim",
        )
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise typer.Exit(result.returncode)
    except KrayneError as exc:
        _state._handle_error(exc)
