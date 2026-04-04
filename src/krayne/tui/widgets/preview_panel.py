"""Cluster preview card shown alongside the explorer table."""

from __future__ import annotations

from rich.text import Text

from textual.widget import Widget

from krayne.api.types import ClusterInfo
from krayne.tui.widgets.cluster_table import _style_status, _services_summary, _age


class PreviewPanel(Widget):
    """Operational preview card for the selected cluster."""

    def __init__(self, **kwargs) -> None:
        super().__init__(id="preview-panel", **kwargs)
        self._cluster: ClusterInfo | None = None
        self._tunnel_active: bool = False

    def update_cluster(
        self,
        cluster: ClusterInfo | None,
        tunnel_active: bool = False,
    ) -> None:
        """Update the preview with a new cluster selection."""
        self._cluster = cluster
        self._tunnel_active = tunnel_active
        self.refresh()

    def render(self) -> str:
        if self._cluster is None:
            return "[dim italic]Select a cluster to preview[/dim italic]"

        c = self._cluster
        lines: list[str] = []

        lines.append(f"[bold]{c.name}[/bold]")
        lines.append(f"[dim]Namespace:[/dim] {c.namespace}")
        lines.append("")
        lines.append(f"Status: {_style_status(c.status)}")
        lines.append(f"Workers: [bold]{c.num_workers}[/bold]")
        lines.append(f"Age: {_age(c.created_at)}")

        if c.head_ip:
            lines.append(f"Head: {c.head_ip}")

        lines.append("")
        lines.append(f"[dim]Services:[/dim] {_services_summary(c)}")

        tunnel_text = "[green]active[/green]" if self._tunnel_active else "[dim]none[/dim]"
        lines.append(f"[dim]Tunnels:[/dim] {tunnel_text}")

        return "\n".join(lines)
