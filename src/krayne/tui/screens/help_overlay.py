"""Contextual help overlay — shows keybindings for the current screen."""

from __future__ import annotations

from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


# Help entries grouped by screen context
_GLOBAL_HELP = [
    ("Navigation", [
        ("?", "Show this help"),
        ("Ctrl+P", "Open command palette"),
        ("q", "Quit / Back"),
        ("Esc", "Close dialog / Go back"),
    ]),
]

_EXPLORER_HELP = [
    ("Cluster Actions", [
        ("Enter", "View cluster details"),
        ("c", "Create a new cluster"),
        ("s", "Scale selected cluster"),
        ("d", "Delete selected cluster"),
        ("t", "Toggle tunnels for cluster"),
    ]),
    ("Browsing", [
        ("/", "Filter clusters"),
        ("Tab", "Cycle sort column"),
        ("n", "Switch namespace"),
        ("r", "Refresh cluster list"),
    ]),
    ("Filters", [
        ("", "Plain text matches cluster name"),
        ("status:", "Filter by status (e.g. status:ready)"),
        ("ns:", "Filter by namespace"),
        ("service:", "Filter by service (e.g. service:notebook)"),
        ("tunnel:", "Filter by tunnel state (active/inactive)"),
    ]),
]

_DETAIL_HELP = [
    ("Actions", [
        ("Esc", "Back to explorer"),
        ("s", "Scale worker group"),
        ("d", "Delete this cluster"),
        ("r", "Refresh details"),
    ]),
    ("Tabs", [
        ("", "Overview \u2014 cluster summary"),
        ("", "Worker Groups \u2014 per-group details and scaling"),
        ("", "Services \u2014 service availability and tunnels"),
        ("", "Tunnels \u2014 per-service tunnel management"),
        ("", "Config \u2014 cluster configuration summary"),
    ]),
]

_CREATE_HELP = [
    ("Create Flow", [
        ("Ctrl+S", "Submit / Create cluster"),
        ("Esc", "Cancel and go back"),
    ]),
]


class HelpOverlay(ModalScreen[None]):
    """Modal overlay showing contextual keybinding reference."""

    BINDINGS = [("escape", "dismiss_help", "Close")]

    def compose(self):
        with Vertical(id="help-dialog"):
            yield Static("[bold]Keyboard Shortcuts[/bold]", classes="dialog-title")
            yield Static("", id="help-content")
            yield Static("\n[dim]Press Escape to close  |  Ctrl+P for command palette[/dim]")

    def on_mount(self) -> None:
        sections = self._get_contextual_help()
        lines: list[str] = []

        for category, entries in sections:
            lines.append(f"\n[bold cyan]{category}[/bold cyan]")
            for key, desc in entries:
                if key:
                    lines.append(f"  [bold]{key:<12}[/bold] {desc}")
                else:
                    lines.append(f"  {'':12} [dim]{desc}[/dim]")

        self.query_one("#help-content", Static).update("\n".join(lines))

    def _get_contextual_help(self) -> list[tuple[str, list[tuple[str, str]]]]:
        """Return help entries relevant to the screen behind this overlay."""
        sections = list(_GLOBAL_HELP)

        # Determine which screen is behind us
        stack = self.app.screen_stack
        if len(stack) >= 2:
            behind = stack[-2]
            from krayne.tui.screens.explorer import ExplorerScreen
            from krayne.tui.screens.detail import ClusterDetailScreen
            from krayne.tui.screens.create_flow import CreateFlowScreen

            if isinstance(behind, ExplorerScreen):
                sections.extend(_EXPLORER_HELP)
            elif isinstance(behind, ClusterDetailScreen):
                sections.extend(_DETAIL_HELP)
            elif isinstance(behind, CreateFlowScreen):
                sections.extend(_CREATE_HELP)
        else:
            # Default to explorer help
            sections.extend(_EXPLORER_HELP)

        return sections

    def action_dismiss_help(self) -> None:
        self.dismiss(None)

    def key_escape(self) -> None:
        self.dismiss(None)
