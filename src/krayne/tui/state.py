"""Centralized reactive state for the Krayne TUI."""

from __future__ import annotations

TERMINAL_COMPACT = "compact"
TERMINAL_STANDARD = "standard"
TERMINAL_WIDE = "wide"


def compute_terminal_class(width: int, height: int) -> str:
    """Return the terminal size class based on current dimensions.

    Column count depends on width alone; height is accepted for API
    stability but does not influence the class. Height-sensitive
    presentation choices (e.g. horizontal preview split) should be
    handled separately.
    """
    if width < 100:
        return TERMINAL_COMPACT
    if width >= 140:
        return TERMINAL_WIDE
    return TERMINAL_STANDARD
