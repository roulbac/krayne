from __future__ import annotations

import dataclasses
import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from prism.api.types import ClusterDetails, ClusterInfo
from prism.sandbox.manager import SandboxStatus
from prism.tunnel import TunnelInfo


def _style_status(status: str) -> str:
    if status in ("ready", "running"):
        return f"[green]{status}[/green]"
    if status in ("pods-pending", "containers-creating", "creating"):
        return f"[yellow]{status}[/yellow]"
    if status in ("image-pull-error", "crash-loop", "unschedulable", "pods-failed"):
        return f"[red]{status}[/red]"
    return f"[dim]{status}[/dim]"


def _build_cluster_panel(info: ClusterInfo) -> Panel:
    ready = info.status in ("ready", "running")
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Name", info.name)
    table.add_row("Namespace", info.namespace)
    table.add_row("Status", _style_status(info.status))
    table.add_row("Cluster Address", info.client_url or "pending")
    table.add_row("Dashboard", info.dashboard_url or "pending")
    if info.notebook_url:
        table.add_row("Notebook", info.notebook_url)
    if info.vscode_url:
        table.add_row("VS Code", info.vscode_url)
    if info.ssh_url:
        table.add_row("SSH", info.ssh_url)
    table.add_row("Workers", str(info.num_workers))
    title = "Cluster Ready" if ready else "Cluster Creating..."
    style = "green" if ready else "yellow"
    return Panel(table, title=title, border_style=style)


def format_cluster_created(
    info: ClusterInfo, console: Console, *, live: bool = False
) -> Panel | None:
    """Print a panel summarising the newly created cluster.

    When *live* is ``True`` the panel is returned instead of printed so it can
    be used with ``rich.live.Live``.
    """
    panel = _build_cluster_panel(info)
    if live:
        return panel
    console.print(panel)
    return None


def format_cluster_list(clusters: list[ClusterInfo], console: Console) -> None:
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
    info = details.info

    # Header
    header = Table(show_header=False, box=None, padding=(0, 2))
    header.add_column("Field", style="bold cyan")
    header.add_column("Value")
    header.add_row("Name", info.name)
    header.add_row("Namespace", info.namespace)
    header.add_row("Status", _style_status(info.status))
    header.add_row("Client", info.client_url or "pending")
    header.add_row("Dashboard", info.dashboard_url or "pending")
    if info.notebook_url:
        header.add_row("Notebook", info.notebook_url)
    if info.vscode_url:
        header.add_row("VS Code", info.vscode_url)
    if info.ssh_url:
        header.add_row("SSH", info.ssh_url)
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
    def _to_dict(obj: Any) -> Any:
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        if isinstance(obj, list):
            return [_to_dict(item) for item in obj]
        return obj

    console.print_json(json.dumps(_to_dict(data), default=str))


def format_init_success(
    kubeconfig_path: str, kube_context: str, console: Console
) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Kubeconfig", kubeconfig_path)
    table.add_row("Context", kube_context)
    console.print(Panel(table, title="Prism Initialized", border_style="green"))


def format_sandbox_setup_success(kubeconfig_path: str, console: Console) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="bold cyan")
    table.add_column("Value")
    table.add_row("Status", "running")
    table.add_row("Kubeconfig", kubeconfig_path)
    console.print(Panel(table, title="Sandbox Ready", border_style="green"))

    hint = Table(show_header=False, box=None, padding=(0, 2))
    hint.add_column("Step", style="bold", justify="right")
    hint.add_column("Command")
    hint.add_row("1.", "[bold]prism init[/bold]  — select the sandbox kubeconfig and context")
    hint.add_row("2.", "[bold]prism create my-cluster[/bold]  — launch your first Ray cluster")
    console.print(Panel(hint, title="Next Steps", border_style="cyan"))


_STATUS_DISPLAY = {
    "pending": "[dim]waiting[/dim]",
    "in_progress": "[yellow]setting up...[/yellow]",
    "done": "[green]✓ ready[/green]",
    "failed": "[red]✗ failed[/red]",
}


def build_sandbox_progress_table(steps: dict[str, str]) -> Table:
    table = Table(
        show_header=True, box=None, padding=(0, 2), title="Sandbox Setup",
    )
    table.add_column("Component", style="bold")
    table.add_column("Status")
    for name, status in steps.items():
        table.add_row(name, _STATUS_DISPLAY.get(status, status))
    return table


def format_sandbox_status(status: SandboxStatus, console: Console) -> None:
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


def format_tunnel_panel(cluster_name: str, tunnels: list[TunnelInfo]) -> Panel:
    """Build a panel showing active tunnel URLs."""
    table = Table(show_header=True, box=None, padding=(0, 2))
    table.add_column("Service", style="bold")
    table.add_column("Local URL", style="green")
    table.add_column("Remote Port", justify="right", style="dim")
    for t in tunnels:
        table.add_row(t.service, t.local_url, str(t.remote_port))
    return Panel(
        table,
        title=f"Tunnel Active \u2014 {cluster_name}",
        subtitle="Press Ctrl+C to stop",
        border_style="green",
    )
