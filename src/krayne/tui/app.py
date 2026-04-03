"""Root Textual application for ikrayne."""

from __future__ import annotations

from pathlib import Path

from textual.app import App
from textual.binding import Binding
from textual.reactive import reactive

CSS_PATH = Path(__file__).parent / "styles" / "ikrayne.tcss"


class IKrayneApp(App):
    """Interactive TUI for managing Ray clusters on Kubernetes."""

    TITLE = "ikrayne"
    SUB_TITLE = "Ray Cluster Manager"
    CSS_PATH = CSS_PATH

    BINDINGS = [
        Binding("q", "quit_or_pop", "Quit", show=True),
        Binding("question_mark", "help", "Help", show=True, key_display="?"),
    ]

    namespace: reactive[str] = reactive("default")

    def on_mount(self) -> None:
        from krayne.tui.screens.cluster_list import ClusterListScreen

        self.push_screen(ClusterListScreen())

    def action_quit_or_pop(self) -> None:
        if len(self.screen_stack) > 2:
            self.pop_screen()
        else:
            self.exit()

    def action_help(self) -> None:
        from krayne.tui.screens.help import HelpScreen

        self.push_screen(HelpScreen())
