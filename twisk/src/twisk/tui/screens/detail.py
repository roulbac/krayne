"""Read-only describe / YAML viewer."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static


def describe_table(fields: dict[str, Any]) -> Table:
    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold #f59e0b", no_wrap=True)
    t.add_column()
    for k, v in fields.items():
        if v is None or v == "" or v == ():
            v = "-"
        if isinstance(v, (list, tuple)):
            v = ", ".join(str(x) for x in v) or "-"
        t.add_row(f"{k}:", v if isinstance(v, (Text, Table)) else str(v))
    return t


def yaml_block(text: str) -> Syntax:
    return Syntax(text.rstrip() or "# (empty)", "yaml", theme="monokai", word_wrap=True, background_color="default")


class DetailScreen(Screen):
    BINDINGS = [
        Binding("escape", "close", "Back"),
        Binding("q", "close", "Back", show=False),
        Binding("c", "copy", "Copy"),
    ]

    def __init__(self, title: str, body: RenderableType | list[RenderableType], copy_text: str | None = None, note: str | None = None) -> None:
        super().__init__()
        self._title = title
        self._body = Group(*body) if isinstance(body, list) else body
        self._copy = copy_text
        self._note = note

    def compose(self) -> ComposeResult:
        note = f"   [dim]{self._note}[/dim]" if self._note else ""
        yield Static(f"[b #f59e0b] {self._title}[/]{note}", id="detail-title")
        with VerticalScroll(id="detail-scroll"):
            yield Static(self._body, id="detail-body")
        yield Footer()

    def action_close(self) -> None:
        self.dismiss()

    def action_copy(self) -> None:
        if self._copy is None:
            self.notify("Nothing to copy here", severity="warning")
            return
        self.app.copy_to_clipboard(self._copy)
        self.notify("Copied to clipboard")
