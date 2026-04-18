"""Global header bar — always visible at the top of every screen."""

from __future__ import annotations

from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static


class HeaderBar(Static):
    """Single-line header showing app name, namespace, view title, and state."""

    namespace: reactive[str] = reactive("default")
    view_title: reactive[str] = reactive("Explorer")
    cluster_name: reactive[str] = reactive("")
    is_loading: reactive[bool] = reactive(False)
    last_refresh: reactive[str] = reactive("")

    def render(self) -> str:
        parts = ["[bold]krayne[/bold]"]
        parts.append(f"[dim]ns:[/dim]{self.namespace}")
        parts.append(f"[bold]{self.view_title}[/bold]")
        if self.cluster_name:
            parts.append(f"[cyan]{self.cluster_name}[/cyan]")
        if self.is_loading:
            parts.append("[yellow]syncing...[/yellow]")
        elif self.last_refresh:
            parts.append(f"[dim]{self.last_refresh}[/dim]")
        return "  ".join(parts)
