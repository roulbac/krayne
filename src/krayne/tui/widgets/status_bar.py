"""Contextual status / action bar — always visible at the bottom."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Bottom bar showing contextual key hints and transient state."""

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._hints: list[tuple[str, str]] = []
        self._message: str = ""

    def set_hints(self, hints: list[tuple[str, str]]) -> None:
        """Set the key action hints. Each hint is (key, description)."""
        self._hints = hints
        self._refresh_content()

    def set_message(self, message: str) -> None:
        """Set a transient status message (cleared on next hint update)."""
        self._message = message
        self._refresh_content()

    def clear_message(self) -> None:
        self._message = ""
        self._refresh_content()

    def _refresh_content(self) -> None:
        parts: list[str] = []
        for key, desc in self._hints:
            parts.append(f"[bold]{key}[/bold] {desc}")
        hint_text = "  ".join(parts)
        if self._message:
            self.update(f"{hint_text}  [dim]|[/dim]  {self._message}")
        else:
            self.update(hint_text)
