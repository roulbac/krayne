"""Rich formatters for CLI output."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prism.api.types import ClusterDetails, ClusterInfo
from prism.sandbox.manager import SandboxStatus


def _style_status(status: str) -> str:
    """Apply Rich markup to a cluster status string."""
    if status in ("ready", "running"):
        return f"[green]{status}[/green]"
    if status in ("pods-pending", "containers-creating", "creating"):
        return f"[yellow]{status}[/yellow]"
    if status in ("image-pull-error", "crash-loop", "unschedulable", "pods-failed"):
        return f"[red]{status}[/red]"
    return f"[dim]{status}[/dim]"


def format_cluster_created(info: ClusterInfo, console: Console) -> None:
    """Print a panel summarising the newly created cluster."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Name", info.name)
    table.add_row("Namespace", info.namespace)
    table.add_row("Status", _style_status(info.status))
    table.add_row("Head IP", info.head_ip or "pending")
    table.add_row("Dashboard", info.dashboard_url or "pending")
    table.add_row("Workers", str(info.num_workers))
    console.print(Panel(table, title="Cluster Created", border_style="green"))


def format_cluster_list(clusters: list[ClusterInfo], console: Console) -> None:
    """Print a table of clusters."""
    table = Table(title="Ray Clusters")
    table.add_column("Name", style="bold")
    table.add_column("Namespace")
    table.add_column("Status")
    table.add_column("Workers", justify="right")
    table.add_column("Created")
    for c in clusters:
        table.add_row(c.name, c.namespace, _style_status(c.status), str(c.num_workers), c.created_at)
    console.print(table)


def format_cluster_details(details: ClusterDetails, console: Console) -> None:
    """Print extended cluster information."""
    info = details.info

    # Header
    header = Table(show_header=False, box=None, padding=(0, 2))
    header.add_column("Field", style="bold cyan")
    header.add_column("Value")
    header.add_row("Name", info.name)
    header.add_row("Namespace", info.namespace)
    header.add_row("Status", _style_status(info.status))
    header.add_row("Head IP", info.head_ip or "pending")
    header.add_row("Dashboard", info.dashboard_url or "pending")
    header.add_row("Ray Version", details.ray_version)
    console.print(Panel(header, title="Cluster Details", border_style="blue"))

    # Head node
    head_table = Table(title="Head Node")
    head_table.add_column("CPUs", justify="right")
    head_table.add_column("Memory")
    head_table.add_column("GPUs", justify="right")
    head_table.add_column("Image")
    head_table.add_row(
        str(details.head.cpus),
        details.head.memory,
        str(details.head.gpus),
        details.head.image,
    )
    console.print(head_table)

    # Worker groups
    if details.worker_groups:
        wg_table = Table(title="Worker Groups")
        wg_table.add_column("Group", style="bold")
        wg_table.add_column("Replicas", justify="right")
        wg_table.add_column("CPUs", justify="right")
        wg_table.add_column("Memory")
        wg_table.add_column("GPUs", justify="right")
        wg_table.add_column("GPU Type")
        for wg in details.worker_groups:
            wg_table.add_row(
                wg.name,
                str(wg.replicas),
                str(wg.cpus),
                wg.memory,
                str(wg.gpus),
                wg.gpu_type or "-",
            )
        console.print(wg_table)


def format_json(data: Any, console: Console) -> None:
    """Print any dataclass (or list of dataclasses) as JSON."""

    def _to_dict(obj: Any) -> Any:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if isinstance(obj, list):
            return [_to_dict(item) for item in obj]
        return obj

    console.print_json(json.dumps(_to_dict(data), default=str))


def format_init_success(kubeconfig_path: str, console: Console) -> None:
    """Print a confirmation panel after ``prism init``."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Kubeconfig", kubeconfig_path)
    console.print(Panel(table, title="Prism Initialized", border_style="green"))


def format_sandbox_setup_success(kubeconfig_path: str, console: Console) -> None:
    """Print a success panel after sandbox setup."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Status", "running")
    table.add_row("Kubeconfig", kubeconfig_path)
    table.add_row("Next step", "prism create my-cluster")
    console.print(Panel(table, title="Sandbox Ready", border_style="green"))


_STATUS_DISPLAY = {
    "pending": "[dim]waiting[/dim]",
    "in_progress": "[yellow]setting up...[/yellow]",
    "done": "[green]✓ ready[/green]",
    "failed": "[red]✗ failed[/red]",
}


def build_sandbox_progress_table(steps: dict[str, str]) -> Table:
    """Build a Rich Table showing sandbox setup component statuses.

    *steps* maps component names to status strings
    (``"pending"``, ``"in_progress"``, ``"done"``, ``"failed"``).
    """
    table = Table(
        show_header=True, box=None, padding=(0, 2), title="Sandbox Setup",
    )
    table.add_column("Component", style="bold")
    table.add_column("Status")
    for name, status in steps.items():
        table.add_row(name, _STATUS_DISPLAY.get(status, status))
    return table


def format_sandbox_status(status: SandboxStatus, console: Console) -> None:
    """Print a panel showing sandbox status."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Running", "yes" if status.running else "no")
    table.add_row("Container ID", status.container_id or "-")
    table.add_row("K3S Image", status.k3s_version or "-")
    table.add_row("Kubeconfig", status.kubeconfig or "-")
    table.add_row("Created", status.created_at or "-")
    style = "green" if status.running else "dim"
    console.print(Panel(table, title="Sandbox Status", border_style=style))
