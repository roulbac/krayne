"""Namespace picker — searchable guided namespace switching."""

from __future__ import annotations

from functools import partial

from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState


def _fetch_namespaces() -> list[str]:
    """Attempt to list namespaces from the cluster."""
    try:
        from krayne.kube.client import get_kube_client

        client = get_kube_client()
        return client.list_namespaces()
    except Exception:
        return []


class NamespacePickerScreen(ModalScreen[str | None]):
    """Searchable namespace picker with recent namespaces."""

    def __init__(self) -> None:
        super().__init__()
        self._all_namespaces: list[str] = []
        self._search_text: str = ""

    def compose(self):
        with Vertical(id="namespace-dialog"):
            yield Static("[bold]Switch Namespace[/bold]", classes="dialog-title")
            yield Input(placeholder="Search namespaces...", id="ns-search-input")
            yield OptionList(id="ns-list")
            yield Static("[dim]Or type a namespace manually:[/dim]")
            yield Input(placeholder="Namespace", id="ns-manual-input")
            with Horizontal(classes="dialog-buttons"):
                yield Button("Switch", variant="primary", id="btn-ns-switch")
                yield Button("Cancel", variant="default", id="btn-ns-cancel")

    def on_mount(self) -> None:
        self.query_one("#ns-search-input", Input).focus()
        # Show recent namespaces immediately
        self._populate_list(self.app.recent_namespaces)
        # Fetch cluster namespaces in background
        self.run_worker(
            _fetch_namespaces,
            thread=True,
            name="fetch_namespaces",
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_namespaces":
            return
        if event.worker.state == WorkerState.SUCCESS:
            fetched = event.worker.result
            # Merge: recent first, then fetched (deduplicated)
            recents = list(self.app.recent_namespaces)
            merged = list(recents)
            for ns in fetched:
                if ns not in merged:
                    merged.append(ns)
            self._all_namespaces = merged
            self._populate_list(merged)

    def _populate_list(self, namespaces: list[str]) -> None:
        option_list = self.query_one("#ns-list", OptionList)
        option_list.clear_options()
        current = self.app.namespace

        for ns in namespaces:
            if self._search_text and self._search_text.lower() not in ns.lower():
                continue
            label = f"[bold]{ns}[/bold] [cyan]\u25c0 current[/cyan]" if ns == current else ns
            option_list.add_option(Option(label, id=ns))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "ns-search-input":
            self._search_text = event.value
            source = self._all_namespaces or list(self.app.recent_namespaces)
            self._populate_list(source)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        ns = event.option_id
        if ns:
            self._switch_to(str(ns))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-ns-switch":
            self._do_switch()
        elif event.button.id == "btn-ns-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "ns-manual-input":
            self._do_switch()
        elif event.input.id == "ns-search-input":
            # Select the first option if available
            option_list = self.query_one("#ns-list", OptionList)
            if option_list.option_count > 0:
                option = option_list.get_option_at_index(0)
                if option.id:
                    self._switch_to(str(option.id))

    def _do_switch(self) -> None:
        manual = self.query_one("#ns-manual-input", Input).value.strip()
        if manual:
            self._switch_to(manual)
        else:
            self.notify("Enter or select a namespace", severity="warning")

    def _switch_to(self, ns: str) -> None:
        self.app.namespace = ns
        self.app.add_recent_namespace(ns)
        self.dismiss(ns)

    def key_escape(self) -> None:
        self.dismiss(None)
