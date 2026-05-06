"""Entry point for the ``krayne tui`` interactive TUI."""

from __future__ import annotations


def main() -> None:
    # Pre-warm `import ray` while the terminal is still in a normal mode.
    # Ray's import chain calls termios.tcsetattr (color/signal setup), which
    # would otherwise corrupt Textual's raw-input + mouse-tracking mode if
    # _get_ray_image() were first called from a worker thread (e.g. during
    # the create-cluster flow).
    from krayne.kube.manifest import _get_ray_image
    _get_ray_image()

    from krayne.tui.app import IKrayneApp

    app = IKrayneApp()
    app.run()


if __name__ == "__main__":
    main()
