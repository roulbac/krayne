"""Diagnose which K8s API call (if any) corrupts terminal mode.

Run in a real terminal: uv run python tests/manual/diag_terminal_mode.py

Writes diagnostic output to /tmp/krayne_termios_diag.log. Open that file
after the script exits to see results.
"""

from __future__ import annotations

import os
import sys
import termios

LOG_PATH = "/tmp/krayne_termios_diag.log"


def attrs_summary(fd: int) -> dict:
    a = termios.tcgetattr(fd)
    return {
        "iflag": a[0],
        "oflag": a[1],
        "cflag": a[2],
        "lflag": a[3],
        "ispeed": a[4],
        "ospeed": a[5],
        "vmin": a[6][termios.VMIN],
        "vtime": a[6][termios.VTIME],
    }


def diff_attrs(before: dict, after: dict) -> list[str]:
    return [f"  {k}: {before[k]!r} -> {after[k]!r}" for k in before if before[k] != after[k]]


def step(log, name: str, fd: int, fn) -> None:
    log.write(f"\n=== {name} ===\n")
    before = attrs_summary(fd)
    try:
        result = fn()
        log.write(f"  result: {type(result).__name__}\n")
    except Exception as exc:
        log.write(f"  exception: {type(exc).__name__}: {exc}\n")
    after = attrs_summary(fd)
    diffs = diff_attrs(before, after)
    if diffs:
        log.write("  ⚠️  TERMIOS CHANGED:\n")
        for d in diffs:
            log.write(d + "\n")
    else:
        log.write("  termios unchanged ✓\n")
    log.flush()


def main() -> int:
    fd = sys.stdout.fileno()
    if not os.isatty(fd):
        print("ERROR: stdout is not a tty — must run in a real terminal")
        return 1

    log = open(LOG_PATH, "w")
    log.write(f"fd={fd}, isatty={os.isatty(fd)}\n")

    saved = termios.tcgetattr(fd)
    try:
        # Put terminal into Textual-like raw mode (no alt-screen, no mouse —
        # we just want to detect termios drift, not corrupt the visible terminal).
        new = list(saved)
        new[3] = new[3] & ~(termios.ECHO | termios.ICANON | termios.IEXTEN | termios.ISIG)
        new[6][termios.VMIN] = 1
        termios.tcsetattr(fd, termios.TCSANOW, new)

        log.write("[Terminal in raw mode]\n")
        log.write(f"Initial termios: {attrs_summary(fd)}\n")

        from krayne.config import ClusterConfig
        from krayne.kube import client as kube_client_mod

        cfg = ClusterConfig(name="diag-test")

        step(log, "import krayne.api.clusters", fd,
             lambda: __import__("krayne.api.clusters", fromlist=["create_cluster"]))

        step(log, "build_manifest", fd, lambda: (
            __import__("krayne.kube.manifest", fromlist=["build_manifest"]).build_manifest(cfg)
        ))

        step(log, "get_kube_client", fd, lambda: kube_client_mod.get_kube_client())

        step(log, "list_ray_clusters (real HTTP call)", fd,
             lambda: kube_client_mod.get_kube_client().list_ray_clusters("default"))

        import subprocess
        step(log, "subprocess.Popen kubectl version (DEVNULL'd)", fd,
             lambda: subprocess.Popen(
                 ["kubectl", "version", "--client"],
                 stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL,
             ).wait())

    finally:
        termios.tcsetattr(fd, termios.TCSANOW, saved)
        log.close()

    print(f"\nDiagnostic complete. Log written to: {LOG_PATH}")
    print(f"Show with:  cat {LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
