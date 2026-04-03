"""Entry point for the ``ikrayne`` interactive TUI."""

from __future__ import annotations


def main() -> None:
    from krayne.tui.app import IKrayneApp

    app = IKrayneApp()
    app.run()


if __name__ == "__main__":
    main()
