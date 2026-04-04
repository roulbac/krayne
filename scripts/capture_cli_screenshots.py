"""Capture CLI command outputs as PNG screenshots for documentation."""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.text import Text

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"

# Commands to capture in lifecycle order.
# tail_lines: if set, only keep the last N lines (for commands with spinner output)
COMMANDS: list[tuple[str, str, list[str], int | None]] = [
    ("cli-sandbox-status", "$ krayne sandbox status", ["krayne", "sandbox", "status"], None),
    ("cli-create", "$ krayne create my-cluster", ["krayne", "create", "my-cluster"], 11),
    ("cli-get", "$ krayne get", ["krayne", "get"], None),
    ("cli-describe", "$ krayne describe my-cluster", ["krayne", "describe", "my-cluster"], None),
    ("cli-scale", "$ krayne scale my-cluster --replicas 3", ["krayne", "scale", "my-cluster", "--replicas", "3"], 11),
    ("cli-tun-open", "$ krayne tun-open my-cluster", ["krayne", "tun-open", "my-cluster"], None),
    ("cli-tun-close", "$ krayne tun-close my-cluster", ["krayne", "tun-close", "my-cluster"], None),
    ("cli-delete", "$ krayne delete my-cluster --force", ["krayne", "delete", "my-cluster", "--force"], None),
    ("cli-error", "$ krayne describe nonexistent-cluster", ["krayne", "describe", "nonexistent-cluster"], None),
]

# Pre-cleanup: delete cluster if it already exists (ignore errors)
CLEANUP_BEFORE = ["krayne", "delete", "my-cluster", "--force"]


def run_command(args: list[str]) -> str:
    """Run a krayne command and return ANSI-colored output."""
    env = {
        **os.environ,
        "FORCE_COLOR": "1",
        "TERM": "xterm-256color",
        "COLUMNS": "80",
    }
    result = subprocess.run(
        ["uv", "run", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return result.stdout + result.stderr


def ansi_to_svg(ansi_output: str, title: str) -> str:
    """Convert ANSI output to SVG via Rich Console."""
    console = Console(record=True, width=80, file=io.StringIO())
    text = Text.from_ansi(ansi_output)
    console.print(text)
    return console.export_svg(title=title)


def svg_to_png(svg_text: str, browser) -> Image.Image:
    """Render SVG to PNG via Playwright."""
    with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", delete=False) as f:
        f.write(svg_text)
        svg_path = f.name

    page = browser.new_page()
    page.goto(f"file://{svg_path}")
    page.wait_for_timeout(500)
    svg_el = page.query_selector("svg")
    box = svg_el.bounding_box()
    png_data = page.screenshot(
        clip={"x": box["x"], "y": box["y"], "width": box["width"], "height": box["height"]}
    )
    page.close()
    Path(svg_path).unlink()
    return Image.open(io.BytesIO(png_data))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Clean up any existing cluster
    print("Cleaning up...")
    run_command(CLEANUP_BEFORE)

    print("Starting browser...")
    pw = sync_playwright().start()
    browser = pw.chromium.launch()

    for filename, title, args, tail_lines in COMMANDS:
        print(f"  Capturing: {title}")
        ansi = run_command(args)
        if tail_lines:
            lines = ansi.splitlines()
            ansi = "\n".join(lines[-tail_lines:])
        svg = ansi_to_svg(ansi, title=title)
        img = svg_to_png(svg, browser)
        out_path = OUT_DIR / f"{filename}.png"
        img.save(out_path, optimize=True)
        print(f"    -> {out_path.name} ({img.size[0]}x{img.size[1]}, {out_path.stat().st_size // 1024}KB)")

    browser.close()
    pw.stop()
    print("\nDone!")


if __name__ == "__main__":
    main()
