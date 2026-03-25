"""Command palette provider for ikrayne."""

from __future__ import annotations

from textual.command import Hit, Hits, Provider


class IKrayneCommands(Provider):
    """Searchable command palette for navigation and actions."""

    async def search(self, query: str) -> Hits:
        app = self.app
        commands = self._get_commands()
        query_lower = query.lower()
        for name, help_text, callback in commands:
            score = self._match_score(query_lower, name.lower())
            if score > 0:
                yield Hit(score, name, callback, help=help_text)

    def _get_commands(self) -> list[tuple[str, str, callable]]:
        app = self.app
        screen_name = type(app.screen).__name__

        commands = [
            ("Go to Explorer", "Return to cluster list", self._go_explorer),
            ("Create cluster", "Open the create cluster flow", self._create),
            ("Switch namespace", "Change the active namespace", self._namespace),
            ("Refresh", "Refresh cluster data", self._refresh),
            ("Open help", "Show keybinding help", self._help),
        ]

        # Context-aware commands when a cluster is selected
        from krayne.tui.screens.explorer import ExplorerScreen

        if isinstance(app.screen, ExplorerScreen):
            commands.extend([
                ("Scale cluster", "Scale worker group replicas", self._scale),
                ("Delete cluster", "Delete the selected cluster", self._delete),
                ("Toggle tunnels", "Open or close tunnels", self._tunnel),
            ])

        from krayne.tui.screens.detail import ClusterDetailScreen

        if isinstance(app.screen, ClusterDetailScreen):
            commands.extend([
                ("Scale cluster", "Scale worker group replicas", self._scale),
                ("Delete cluster", "Delete this cluster", self._delete),
            ])

        return commands

    @staticmethod
    def _match_score(query: str, target: str) -> float:
        if not query:
            return 1.0
        if query in target:
            return 90.0 - len(target)
        # Fuzzy: check if all query chars appear in order
        qi = 0
        for ch in target:
            if qi < len(query) and ch == query[qi]:
                qi += 1
        if qi == len(query):
            return 50.0 - len(target)
        return 0.0

    def _go_explorer(self) -> None:
        from krayne.tui.screens.explorer import ExplorerScreen

        if not isinstance(self.app.screen, ExplorerScreen):
            # Pop back to explorer
            while len(self.app.screen_stack) > 2:
                self.app.pop_screen()

    def _create(self) -> None:
        from krayne.tui.screens.create_flow import CreateFlowScreen

        self.app.push_screen(CreateFlowScreen())

    def _namespace(self) -> None:
        from krayne.tui.screens.namespace_picker import NamespacePickerScreen

        self.app.push_screen(NamespacePickerScreen())

    def _refresh(self) -> None:
        self.app.screen.run_action("refresh")

    def _help(self) -> None:
        from krayne.tui.screens.help_overlay import HelpOverlay

        self.app.push_screen(HelpOverlay())

    def _scale(self) -> None:
        self.app.screen.run_action("scale")

    def _delete(self) -> None:
        self.app.screen.run_action("delete")

    def _tunnel(self) -> None:
        self.app.screen.run_action("tunnel")
