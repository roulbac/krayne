"""Built-in YAML editor — the escape hatch when the form isn't enough.

Used when ``$EDITOR`` is not set (otherwise twisk suspends and opens your
editor on the file in the spec library).
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Static, TextArea

from twisk.specs.models import Spec, SpecKind, spec_from_yaml
from twisk.specs.store import _short_error


@dataclass(frozen=True)
class EditorResult:
    text: str
    spec: Spec
    launch: bool


class YamlEditorScreen(Screen[EditorResult | None]):
    BINDINGS = [
        Binding("ctrl+s", "save(True)", "Save & launch", priority=True),
        Binding("ctrl+o", "save(False)", "Save only", priority=True),
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    def __init__(self, kind: SpecKind, name: str, text: str, path: str | None = None) -> None:
        super().__init__()
        self.kind = kind
        self.spec_name = name
        self.text = text
        self.path = path

    def compose(self) -> ComposeResult:
        where = f"  [dim]{self.path}[/dim]" if self.path else ""
        yield Static(f"[b #f59e0b] YAML · {self.kind.title.lower()} {self.spec_name}[/]{where}", id="editor-title")
        try:
            area = TextArea.code_editor(self.text, language="yaml", id="editor", soft_wrap=False)
        except Exception:  # noqa: BLE001 - no tree-sitter grammar available
            area = TextArea.code_editor(self.text, id="editor", soft_wrap=False)
        yield area
        yield Static("[dim]SkyPilot task YAML · validated on save · any SkyPilot field is allowed[/dim]", id="editor-status")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#editor", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        status = self.query_one("#editor-status", Static)
        try:
            spec_from_yaml(self.kind, self.spec_name, event.text_area.text)
        except Exception as exc:  # noqa: BLE001
            status.update(f"[red]✗ {_short_error(exc)}[/red]")
        else:
            status.update("[green]✓ valid[/green]")

    def action_save(self, launch: bool) -> None:
        text = self.query_one("#editor", TextArea).text
        try:
            spec = spec_from_yaml(self.kind, self.spec_name, text)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Not saved: {_short_error(exc)}", severity="error", timeout=6)
            return
        self.dismiss(EditorResult(text, spec, launch))

    def action_cancel(self) -> None:
        self.dismiss(None)
