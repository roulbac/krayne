"""Capture TUI screenshots and combine into a GIF for documentation."""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from unittest.mock import patch

import cairosvg
from PIL import Image

from krayne.api.types import ClusterDetails, ClusterInfo, HeadNodeInfo, WorkerGroupInfo
from krayne.tui.app import IKrayneApp

# ── Mock data ────────────────────────────────────────

CLUSTERS = [
    ClusterInfo(
        name="prod-training",
        namespace="default",
        status="ready",
        head_ip="10.42.0.15",
        dashboard_url="http://10.42.0.15:8265",
        client_url="ray://10.42.0.15:10001",
        notebook_url="http://10.42.0.15:8888",
        code_server_url="http://10.42.0.15:8443",
        ssh_url=None,
        num_workers=4,
        created_at="2026-04-03T08:30:00Z",
    ),
    ClusterInfo(
        name="dev-experiment",
        namespace="default",
        status="running",
        head_ip="10.42.0.22",
        dashboard_url="http://10.42.0.22:8265",
        client_url="ray://10.42.0.22:10001",
        notebook_url=None,
        code_server_url=None,
        ssh_url=None,
        num_workers=2,
        created_at="2026-04-03T10:15:00Z",
    ),
    ClusterInfo(
        name="staging-pipeline",
        namespace="default",
        status="creating",
        head_ip=None,
        dashboard_url=None,
        client_url=None,
        notebook_url=None,
        code_server_url=None,
        ssh_url=None,
        num_workers=1,
        created_at="2026-04-03T11:45:00Z",
    ),
]

DETAILS = ClusterDetails(
    info=CLUSTERS[0],
    head=HeadNodeInfo(cpus="4", memory="8Gi", gpus=0, image="rayproject/ray:2.10.0-py311"),
    worker_groups=[
        WorkerGroupInfo(name="gpu-workers", replicas=4, cpus="2", memory="16Gi", gpus=1, gpu_type="a100"),
    ],
    ray_version="2.10.0",
    python_version="3.11",
)

SERVICES = ["dashboard", "notebook", "client", "code-server"]

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"


def svg_to_pil(svg_text: str, scale: float = 1.0) -> Image.Image:
    """Convert SVG text to a PIL Image."""
    png_data = cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), scale=scale)
    return Image.open(io.BytesIO(png_data))


async def _pause(pilot, n: int = 3) -> None:
    for _ in range(n):
        await pilot.pause()


async def _switch_tab(app, tabs_id: str, tab_id: str, pilot) -> None:
    """Switch tab without focus snap-back."""
    app.screen.set_focus(None)
    tabs = app.screen.query_one(f"#{tabs_id}")
    tabs.active = tab_id
    await _pause(pilot)


async def capture_frames() -> list[Image.Image]:
    """Drive the TUI and capture screenshots at key moments."""
    frames: list[Image.Image] = []

    explorer_patches = [
        patch("krayne.tui.screens.explorer.list_clusters", return_value=CLUSTERS),
        patch("krayne.tui.screens.explorer.is_tunnel_active", return_value=False),
    ]

    detail_patches = [
        patch("krayne.tui.screens.detail.describe_cluster", return_value=DETAILS),
        patch("krayne.tui.screens.detail.get_cluster_services", return_value=SERVICES),
        patch("krayne.tui.screens.detail.is_tunnel_active", return_value=False),
        patch("krayne.tui.screens.detail.load_tunnel_state", return_value=None),
    ]

    all_patches = explorer_patches + detail_patches
    for p in all_patches:
        p.start()
    try:
        app = IKrayneApp()
        async with app.run_test(size=(100, 30)) as pilot:
            # ── Frame 1: Cluster explorer ──────────────
            await _pause(pilot, 5)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 2: Open create form (Cluster tab) ──
            await pilot.press("c")
            await _pause(pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 3: Head Node tab ─────────────────
            await _switch_tab(app, "create-tabs", "tab-head", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 4: Workers tab ───────────────────
            await _switch_tab(app, "create-tabs", "tab-workers", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 5: Services tab ──────────────────
            await _switch_tab(app, "create-tabs", "tab-services", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 6: Review tab ────────────────────
            # Fill in the name first so review shows a summary
            app.screen.query_one("#input-name").value = "my-cluster"
            await _switch_tab(app, "create-tabs", "tab-review", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 7: Back to explorer ──────────────
            await pilot.press("escape")
            await _pause(pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 8: Detail screen (Overview) ──────
            table = app.screen.query_one("DataTable")
            table.focus()
            await pilot.pause()
            await pilot.press("enter")
            await _pause(pilot, 5)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 9: Detail Workers tab ────────────
            await _switch_tab(app, "detail-tabs", "tab-workers", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))

            # ── Frame 10: Detail Services tab ──────────
            await _switch_tab(app, "detail-tabs", "tab-services", pilot)
            frames.append(svg_to_pil(app.export_screenshot()))
    finally:
        for p in all_patches:
            p.stop()

    return frames


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gif_path = OUT_DIR / "ikrayne-demo.gif"

    print("Capturing TUI frames...")
    frames = asyncio.run(capture_frames())
    print(f"Captured {len(frames)} frames")

    if not frames:
        print("No frames captured!", file=sys.stderr)
        sys.exit(1)

    # Save individual frames as PNG for reference
    for i, frame in enumerate(frames):
        frame.save(OUT_DIR / f"ikrayne-frame-{i}.png")
        print(f"  Saved frame {i}: {frame.size}")

    # Durations: longer on explorer/detail/review, shorter on form tabs
    durations = [
        3000,  # explorer
        2000,  # create: cluster tab
        2000,  # create: head node tab
        2000,  # create: workers tab
        2000,  # create: services tab
        3000,  # create: review tab
        1500,  # back to explorer
        3000,  # detail: overview
        2000,  # detail: workers
        2000,  # detail: services
    ]
    durations = durations[: len(frames)]

    frames[0].save(
        gif_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"\nGIF saved to: {gif_path}")
    print(f"Size: {gif_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
