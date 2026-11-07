"""Root Textual application."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from textual.app import App
from textual.binding import Binding

from stratus.backend.base import Backend
from stratus.specs.store import SpecStore


class StratusApp(App):
    TITLE = "stratus"
    CSS_PATH = Path(__file__).parent / "stratus.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", show=False, priority=True)]

    def __init__(
        self,
        backend: Backend,
        store: SpecStore | None = None,
        refresh_seconds: float = 3.0,
        initial_view: str = "clusters",
        endpoint_source: str = "",
        editor: str | None = None,
    ) -> None:
        super().__init__()
        self.backend = backend
        self.store = store or SpecStore()
        self.refresh_seconds = max(0.5, float(refresh_seconds))
        self.initial_view = initial_view
        self.endpoint_source = endpoint_source
        self.external_editor = editor if editor is not None else (os.environ.get("VISUAL") or os.environ.get("EDITOR"))
        # Cached for form suggestions; refreshed in the background.
        self.infra_cache: list[str] = []
        self.volume_cache: list[str] = []
        self.pool_cache: list[str] = []
        self.workspace: str | None = None

    def on_mount(self) -> None:
        from stratus.tui.screens.main import MainScreen

        self.push_screen(MainScreen(self.initial_view))
        self._warm_caches()
        self.set_interval(60, self._warm_caches)

    def _warm_caches(self) -> None:
        def work() -> None:
            try:
                if not self.backend.server_info().healthy:
                    return
                infra = [_infra_hint(r.name) for r in self.backend.list_infra()]
                vols = [v.name for v in self.backend.list_volumes()]
            except Exception:  # noqa: BLE001 - suggestions are best-effort
                return
            self.infra_cache = sorted(set(i for i in infra if i))
            self.volume_cache = vols
            for fetch, attr in ((self.backend.list_pools, "pool_cache"),):
                try:
                    setattr(self, attr, [p.name for p in fetch()])
                except Exception:  # noqa: BLE001
                    pass
            try:
                active = next((w.name for w in self.backend.list_workspaces() if w.active), None)
            except Exception:  # noqa: BLE001
                active = None
            if active:
                self.workspace = active
                self.call_from_thread(self._show_workspace, active)

        threading.Thread(target=work, name="warm-caches", daemon=True).start()

    def _show_workspace(self, name: str) -> None:
        from stratus.tui.widgets.header import Header

        for screen in self.screen_stack:
            for header in screen.query(Header):
                header.set_scope(workspace=name)

    def save_workspace(self, name: str) -> None:
        """Remember the workspace for the next stratus session."""
        from stratus.settings import load_settings, save_settings

        settings = load_settings()
        settings.workspace = name
        save_settings(settings)


def _infra_hint(name: str) -> str:
    """'Kubernetes/my-ctx' → 'k8s/my-ctx', 'AWS' → 'aws'."""
    lowered = name.strip()
    if lowered.lower().startswith("kubernetes"):
        rest = lowered[len("kubernetes"):].lstrip("/")
        return "k8s" + (f"/{rest}" if rest else "")
    return lowered.lower()
