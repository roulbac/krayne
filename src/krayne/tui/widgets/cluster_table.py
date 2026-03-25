"""Enhanced cluster table with responsive columns, sorting, and stable refresh."""

from __future__ import annotations

from textual.widgets import DataTable

from krayne.api.types import ClusterInfo
from krayne.tui.state import TERMINAL_COMPACT, TERMINAL_STANDARD, TERMINAL_WIDE


def _style_status(status: str) -> str:
    if status in ("ready", "running"):
        return f"[green]{status}[/green]"
    if status in ("pods-pending", "containers-creating", "creating"):
        return f"[yellow]{status}[/yellow]"
    if status in ("image-pull-error", "crash-loop", "unschedulable", "pods-failed"):
        return f"[red]{status}[/red]"
    return f"[dim]{status}[/dim]"


def _services_summary(cluster: ClusterInfo) -> str:
    """Compact service availability summary."""
    services = []
    if cluster.dashboard_url:
        services.append("dash")
    if cluster.notebook_url:
        services.append("nb")
    if cluster.client_url:
        services.append("ray")
    if cluster.code_server_url:
        services.append("code")
    if cluster.ssh_url:
        services.append("ssh")
    return ", ".join(services) if services else "[dim]none[/dim]"


def _age(created_at: str) -> str:
    """Compute a human-readable age from ISO timestamp."""
    from datetime import datetime, timezone

    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - created
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}m"
        hours = minutes // 60
        if hours < 24:
            return f"{hours}h"
        days = hours // 24
        return f"{days}d"
    except (ValueError, TypeError):
        return created_at


# Column definitions per terminal class
COLUMNS_COMPACT = ["Name", "Status", "Workers"]
COLUMNS_STANDARD = ["Name", "Namespace", "Status", "Workers", "Age", "Services"]
COLUMNS_WIDE = [
    "Name", "Namespace", "Status", "Workers", "Age", "Services", "Tunnels",
]

_COLUMNS_BY_CLASS = {
    TERMINAL_COMPACT: COLUMNS_COMPACT,
    TERMINAL_STANDARD: COLUMNS_STANDARD,
    TERMINAL_WIDE: COLUMNS_WIDE,
}

# Sort keys
SORT_KEYS = {
    "Name": lambda c: c.name.lower(),
    "Namespace": lambda c: c.namespace.lower(),
    "Status": lambda c: c.status,
    "Workers": lambda c: c.num_workers,
    "Age": lambda c: c.created_at,
    "Services": lambda c: _services_summary(c),
    "Tunnels": lambda c: "",
}


class ClusterTable(DataTable):
    """DataTable with responsive columns and stable refresh."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_type = "row"
        self._current_columns: list[str] = []
        self._sort_column: str = "Name"
        self._sort_reverse: bool = False
        self._tunnel_states: dict[str, bool] = {}

    def setup_columns(self, terminal_class: str) -> None:
        """Reconfigure columns for the given terminal size class."""
        cols = _COLUMNS_BY_CLASS.get(terminal_class, COLUMNS_STANDARD)
        if cols == self._current_columns:
            return
        self._current_columns = cols
        self.clear(columns=True)
        for col in cols:
            self.add_column(col, key=col)

    def set_tunnel_states(self, states: dict[str, bool]) -> None:
        """Update tunnel active states keyed by 'namespace/name'."""
        self._tunnel_states = states

    def rebuild(
        self,
        clusters: list[ClusterInfo],
        filter_fn: callable | None = None,
    ) -> None:
        """Rebuild table rows, preserving cursor position by row key."""
        # Save current selection
        selected_key = self._get_selected_key()

        # Filter
        visible = clusters
        if filter_fn:
            visible = [c for c in clusters if filter_fn(c)]

        # Sort
        sort_fn = SORT_KEYS.get(self._sort_column)
        if sort_fn:
            visible = sorted(visible, key=sort_fn, reverse=self._sort_reverse)

        # Rebuild rows
        self.clear()
        for cluster in visible:
            row_key = f"{cluster.namespace}/{cluster.name}"
            row = self._make_row(cluster, row_key)
            self.add_row(*row, key=row_key)

        # Restore selection
        if selected_key:
            self._restore_selection(selected_key)

    def cycle_sort(self) -> str:
        """Cycle to the next sort column. Returns the new column name."""
        if not self._current_columns:
            return self._sort_column
        try:
            idx = self._current_columns.index(self._sort_column)
            idx = (idx + 1) % len(self._current_columns)
        except ValueError:
            idx = 0
        self._sort_column = self._current_columns[idx]
        return self._sort_column

    def toggle_sort_direction(self) -> None:
        self._sort_reverse = not self._sort_reverse

    def _make_row(self, cluster: ClusterInfo, row_key: str) -> list[str]:
        row: list[str] = []
        for col in self._current_columns:
            if col == "Name":
                row.append(cluster.name)
            elif col == "Namespace":
                row.append(cluster.namespace)
            elif col == "Status":
                row.append(_style_status(cluster.status))
            elif col == "Workers":
                row.append(str(cluster.num_workers))
            elif col == "Age":
                row.append(_age(cluster.created_at))
            elif col == "Services":
                row.append(_services_summary(cluster))
            elif col == "Tunnels":
                active = self._tunnel_states.get(row_key, False)
                row.append("[green]active[/green]" if active else "[dim]none[/dim]")
            else:
                row.append("")
        return row

    def _get_selected_key(self) -> str | None:
        if self.row_count == 0:
            return None
        try:
            row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
            return row_key.value
        except Exception:
            return None

    def _restore_selection(self, key: str) -> None:
        """Move cursor to the row with the given key."""
        for idx, row_key in enumerate(self.rows):
            if row_key.value == key:
                self.move_cursor(row=idx)
                return

    def get_selected_key(self) -> str | None:
        """Return the row key of the currently selected row."""
        return self._get_selected_key()
