"""Help overlay screen."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP_ENTRIES = [
    ("c", "Create a new cluster"),
    ("Enter", "View cluster details"),
    ("s", "Scale selected cluster"),
    ("d", "Delete selected cluster"),
    ("t", "Toggle tunnel open/close"),
    ("/", "Filter clusters by name"),
    ("n", "Switch namespace"),
    ("r", "Refresh cluster list"),
    ("q", "Quit / Back"),
    ("?", "Show this help"),
    ("Esc", "Close dialog / Go back"),
]


class HelpScreen(ModalScreen[None]):
    """Modal overlay showing keybinding reference."""

    BINDINGS = [("escape", "dismiss_help", "Close")]

    def compose(self):
        with Vertical(id="help-dialog"):
            yield Static("[bold]Keybindings[/bold]", classes="dialog-title")
            lines: list[str] = []
            for key, desc in _HELP_ENTRIES:
                lines.append(f"  [bold cyan]{key:<8}[/bold cyan] {desc}")
            yield Static("\n".join(lines))
            yield Static("\n[dim]Press Escape to close[/dim]")

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)
