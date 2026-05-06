"""Shared base class and helpers for Krayne TUI screens."""

from __future__ import annotations

from textual.screen import Screen

from krayne.tunnel import is_tunnel_active, start_tunnels, stop_tunnels
from krayne.tui.widgets.status_bar import StatusBar


class KrayneScreen(Screen):
    """Base screen that wires the app-level `terminal_class` reactive.

    Subclasses that override `on_mount` must call `super().on_mount()`,
    and may override `_after_terminal_class_change` to react to layout
    changes (e.g. rebuilding a table).
    """

    def on_mount(self) -> None:
        self.add_class(self.app.terminal_class)
        # Textual does not auto-wire `watch_app_<name>`; register
        # explicitly so the screen reacts to terminal resize.
        self.watch(self.app, "terminal_class", self._on_terminal_class_change, init=False)

    def _on_terminal_class_change(self, old: str, new: str) -> None:
        self.remove_class(old)
        self.add_class(new)
        self._after_terminal_class_change(old, new)

    def _after_terminal_class_change(self, old: str, new: str) -> None:
        """Hook for subclasses; default is a no-op."""

    def _set_status_hints(self, hints: list[tuple[str, str]]) -> None:
        self.query_one(StatusBar).set_hints(hints)


def toggle_cluster_tunnels(name: str, namespace: str, services: list[str]) -> str:
    """Open all tunnels if none are active, else close them.

    Returns a human-readable status message suitable for `notify`.
    """
    if is_tunnel_active(name, namespace):
        stop_tunnels(name, namespace)
        return f"Tunnels closed for {name}"
    start_tunnels(name, namespace, services)
    return f"Tunnels opened for {name}"
