"""Prism CLI — thin Typer shell over the SDK."""

from __future__ import annotations

import traceback
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from prism import __version__
from prism.errors import PrismError

app = typer.Typer(
    name="prism",
    help="Create, manage, and scale Ray clusters on Kubernetes.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

# Global options stored on the context
_debug: bool = False
_output_json: bool = False


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"prism {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
) -> None:
    """Prism — Ray clusters made simple."""
    global _debug, _output_json
    _debug = debug
    _output_json = output == "json"


def _handle_error(exc: Exception) -> None:
    if _debug:
        err_console.print_exception()
    else:
        err_console.print(Panel(str(exc), title="Error", border_style="red"))
    raise typer.Exit(1)


# -- Commands ----------------------------------------------------------------

from prism.api import (  # noqa: E402
    create_cluster as _create_cluster,
    delete_cluster as _delete_cluster,
    describe_cluster as _describe_cluster,
    get_cluster as _get_cluster,
    list_clusters as _list_clusters,
    scale_cluster as _scale_cluster,
)
from prism.config import ClusterConfig, WorkerGroupConfig, load_config_from_yaml  # noqa: E402
from prism.output import (  # noqa: E402
    format_cluster_created,
    format_cluster_details,
    format_cluster_list,
    format_json,
)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    gpus_per_worker: int = typer.Option(0, "--gpus-per-worker"),
    worker_gpu_type: str = typer.Option("t4", "--worker-gpu-type"),
    cpus_in_head: int = typer.Option(15, "--cpus-in-head"),
    memory_in_head: str = typer.Option("48Gi", "--memory-in-head"),
    workers: int = typer.Option(1, "--workers"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for the cluster to be ready."),
    timeout: int = typer.Option(300, "--timeout"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="YAML config file."),
) -> None:
    """Create a new Ray cluster."""
    try:
        if file:
            overrides = {"name": name, "namespace": namespace}
            config = load_config_from_yaml(file, overrides=overrides)
        else:
            config = ClusterConfig(
                name=name,
                namespace=namespace,
                head={"cpus": cpus_in_head, "memory": memory_in_head},  # type: ignore[arg-type]
                worker_groups=[
                    WorkerGroupConfig(
                        replicas=workers,
                        gpus=gpus_per_worker,
                        gpu_type=worker_gpu_type,
                    )
                ],
            )

        info = _create_cluster(config, wait=wait, timeout=timeout)
        if _output_json:
            format_json(info, console)
        else:
            format_cluster_created(info, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("get")
def get(
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """List Ray clusters in a namespace."""
    try:
        clusters = _list_clusters(namespace)
        if _output_json:
            format_json(clusters, console)
        else:
            format_cluster_list(clusters, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("describe")
def describe(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """Show detailed information about a cluster."""
    try:
        details = _describe_cluster(name, namespace)
        if _output_json:
            format_json(details, console)
        else:
            format_cluster_details(details, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("scale")
def scale(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    worker_group: str = typer.Option("worker", "--worker-group", "-g"),
    replicas: int = typer.Option(..., "--replicas", "-r", help="Target replica count."),
) -> None:
    """Scale a worker group of a cluster."""
    try:
        info = _scale_cluster(name, namespace, worker_group, replicas)
        if _output_json:
            format_json(info, console)
        else:
            format_cluster_created(info, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Delete a Ray cluster."""
    try:
        if not force:
            typer.confirm(
                f"Delete cluster '{name}' in namespace '{namespace}'?", abort=True
            )
        _delete_cluster(name, namespace)
        console.print(f"Cluster '{name}' deleted.", style="green")
    except PrismError as exc:
        _handle_error(exc)
