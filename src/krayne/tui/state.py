"""Centralized reactive state for the Krayne TUI."""

from __future__ import annotations

TERMINAL_COMPACT = "compact"
TERMINAL_STANDARD = "standard"
TERMINAL_WIDE = "wide"


def compute_terminal_class(width: int, height: int) -> str:
    """Return the terminal size class based on current dimensions."""
    if width < 100 or height < 30:
        return TERMINAL_COMPACT
    if width >= 140 and height >= 40:
        return TERMINAL_WIDE
    return TERMINAL_STANDARD
