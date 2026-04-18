"""Root Textual application for the Krayne TUI."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.events import Resize
from textual.reactive import reactive

from krayne.tui.commands import IKrayneCommands
from krayne.tui.state import compute_terminal_class, TERMINAL_STANDARD

CSS_PATH = Path(__file__).parent / "styles" / "ikrayne.tcss"


class IKrayneApp(App):
    """Interactive TUI for managing Ray clusters on Kubernetes."""

    TITLE = "krayne"
    SUB_TITLE = "Ray Cluster Manager"
    CSS_PATH = CSS_PATH
    COMMANDS = {IKrayneCommands}

    BINDINGS = [
        Binding("q", "quit_or_pop", "Quit", show=False),
        Binding("question_mark", "help", "Help", show=False, key_display="?"),
    ]

    namespace: reactive[str] = reactive("default")
    recent_namespaces: reactive[list] = reactive(list, always_update=True)
    terminal_class: reactive[str] = reactive(TERMINAL_STANDARD)
    selected_cluster: reactive[str] = reactive("")
    is_loading: reactive[bool] = reactive(False)
    last_refresh: reactive[str] = reactive("")

    def on_mount(self) -> None:
        self.recent_namespaces = ["default"]
        self._update_terminal_class()
        from krayne.tui.screens.explorer import ExplorerScreen

        self.push_screen(ExplorerScreen())

    def on_resize(self, event: Resize) -> None:
        self._update_terminal_class()

    def _update_terminal_class(self) -> None:
        new_class = compute_terminal_class(self.size.width, self.size.height)
        if new_class != self.terminal_class:
            old = self.terminal_class
            self.terminal_class = new_class
            # Update CSS class on the screen
            self.screen.remove_class(old)
            self.screen.add_class(new_class)

    def add_recent_namespace(self, ns: str) -> None:
        """Track a namespace as recently used."""
        recents = list(self.recent_namespaces)
        if ns in recents:
            recents.remove(ns)
        recents.insert(0, ns)
        self.recent_namespaces = recents[:10]

    def action_quit_or_pop(self) -> None:
        if len(self.screen_stack) > 2:
            self.pop_screen()
        else:
            self.exit()

    def action_help(self) -> None:
        from krayne.tui.screens.help_overlay import HelpOverlay

        self.push_screen(HelpOverlay())
