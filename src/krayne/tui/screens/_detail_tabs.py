"""Per-tab content widgets for the cluster detail screen.

Each widget owns the rendering of one tab and exposes a single `update_data`
method. Keeping these out of `detail.py` lets the screen focus on
orchestration (data fetching, worker handling, key bindings).
"""

from __future__ import annotations

from textual.widgets import Static

from krayne.api.types import ClusterDetails
from krayne.tunnel import SERVICE_PORTS
from krayne.tui.widgets.cluster_table import _age, _style_status


class OverviewTab(Static):
    """Cluster summary, service availability, and tunnel state."""

    def update_data(
        self,
        details: ClusterDetails,
        services: list[str],
        tunnel_map: dict[str, str],
        health: dict[str, str] | None = None,
    ) -> None:
        info = details.info
        health = health or {}

        lines: list[str] = []
        lines.append(f"[bold]{info.name}[/bold]")
        lines.append("")
        lines.append(f"  [dim]Status:[/dim]     {_style_status(info.status)}")
        lines.append(f"  [dim]Namespace:[/dim]  {info.namespace}")
        lines.append(f"  [dim]Age:[/dim]        {_age(info.created_at)}")
        lines.append(f"  [dim]Workers:[/dim]    {info.num_workers}")
        lines.append(f"  [dim]Ray:[/dim]        {details.ray_version}")
        if info.head_ip:
            lines.append(f"  [dim]Head IP:[/dim]   {info.head_ip}")

        lines.append("")
        lines.append("[bold]Services[/bold]")
        for svc in ["dashboard", "notebook", "client", "code-server", "ssh"]:
            if svc not in services:
                continue
            svc_status = health.get(svc, "available")
            if svc_status == "available":
                lines.append(f"  [green]●[/green] {svc}")
            elif svc_status == "pending":
                lines.append(f"  [yellow]○[/yellow] {svc} [dim](starting)[/dim]")
            else:  # unreachable
                lines.append(f"  [red]●[/red] {svc} [dim](unreachable)[/dim]")

        lines.append("")
        lines.append("[bold]Tunnels[/bold]")
        if tunnel_map:
            for svc, url in tunnel_map.items():
                lines.append(f"  [green]●[/green] {svc}: {url}")
        else:
            lines.append("  [dim]No active tunnels[/dim]")

        self.update("\n".join(lines))


class WorkersTab(Static):
    """List of worker groups with replica/resource info."""

    def update_data(self, details: ClusterDetails) -> None:
        if not details.worker_groups:
            self.update("[dim]No worker groups configured[/dim]")
            return

        lines: list[str] = []
        for wg in details.worker_groups:
            lines.append(f"[bold]{wg.name}[/bold]")
            lines.append(f"  [dim]Replicas:[/dim] {wg.replicas}")
            lines.append(f"  [dim]CPUs:[/dim]     {wg.cpus}")
            lines.append(f"  [dim]Memory:[/dim]   {wg.memory}")
            lines.append(f"  [dim]GPUs:[/dim]     {wg.gpus}")
            lines.append("")

        self.update("\n".join(lines))


class ServicesTab(Static):
    """Per-service availability and tunnel endpoint summary."""

    def update_data(
        self,
        details: ClusterDetails,
        services: list[str],
        tunnel_map: dict[str, str],
        health: dict[str, str] | None = None,
    ) -> None:
        info = details.info
        health = health or {}

        lines: list[str] = []
        for svc_name in SERVICE_PORTS:
            url_attr = svc_name.replace("-", "_") + "_url"
            endpoint = getattr(info, url_attr, None)
            declared = svc_name in services
            tunnel_url = tunnel_map.get(svc_name)
            tunnel_active = svc_name in tunnel_map

            if not declared:
                status = "[dim]○ not configured[/dim]"
            else:
                svc_status = health.get(svc_name, "available")
                if svc_status == "available":
                    status = "[green]●[/green] available"
                elif svc_status == "pending":
                    status = "[yellow]○[/yellow] pending"
                else:
                    status = "[red]●[/red] unreachable"

            if tunnel_active and tunnel_url:
                ep = f"[cyan]{tunnel_url}[/cyan]"
            elif endpoint:
                ep = f"[dim]{endpoint}[/dim]"
            else:
                ep = "[dim]—[/dim]"

            if tunnel_active:
                tun = "[green]tunnel open[/green]"
            elif declared:
                tun = "[dim]tunnel closed[/dim]"
            else:
                tun = ""

            lines.append(f"  [bold]{svc_name:<14}[/bold] {status:<30} {ep}  {tun}")

        if not lines:
            lines.append("[dim]No services detected[/dim]")

        self.update("\n".join(lines))


class TunnelsTab(Static):
    """Per-service tunnel control overview."""

    def update_data(
        self,
        services: list[str],
        tunnel_map: dict[str, str],
    ) -> None:
        lines: list[str] = []
        lines.append("[bold]Per-Service Tunnel Control[/bold]")
        lines.append("[dim]Use the Services tab buttons or these keyboard shortcuts:[/dim]")
        lines.append("")

        for svc_name in SERVICE_PORTS:
            available = svc_name in services
            tunnel_url = tunnel_map.get(svc_name)
            tunnel_active = svc_name in tunnel_map

            if tunnel_active and tunnel_url:
                status = f"[green]● active[/green]  {tunnel_url}"
            elif available:
                status = "[dim]○ closed[/dim]"
            else:
                status = "[dim]— unavailable[/dim]"

            lines.append(f"  [bold]{svc_name:<14}[/bold] {status}")

        lines.append("")
        if tunnel_map:
            lines.append(f"[dim]{len(tunnel_map)} tunnel(s) active[/dim]")
        else:
            lines.append("[dim]No active tunnels[/dim]")

        self.update("\n".join(lines))


class ConfigTab(Static):
    """Read-only view of the cluster's spec."""

    def update_data(self, details: ClusterDetails, services: list[str]) -> None:
        lines: list[str] = []
        lines.append("[bold]Head Node[/bold]")
        lines.append(
            f"  CPUs: {details.head.cpus}    "
            f"Memory: {details.head.memory}    "
            f"GPUs: {details.head.gpus}"
        )
        lines.append(
            f"  Runs tasks: {'yes' if details.head.runs_tasks else 'no (control plane)'}"
        )
        lines.append(f"  Image: {details.head.image}")

        lines.append("")
        lines.append("[bold]Worker Groups[/bold]")
        for wg in details.worker_groups:
            lines.append(f"  [bold]{wg.name}[/bold]")
            parts = [f"Replicas: {wg.replicas}", f"CPUs: {wg.cpus}", f"Memory: {wg.memory}"]
            if wg.gpus:
                parts.append(f"GPUs: {wg.gpus}")
            lines.append(f"    {', '.join(parts)}")

        lines.append("")
        lines.append("[bold]Services[/bold]")
        if services:
            for svc in services:
                lines.append(f"  ✓ {svc}")
        else:
            lines.append("  [dim]None detected[/dim]")

        self.update("\n".join(lines))
