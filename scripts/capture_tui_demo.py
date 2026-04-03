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

OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "assets"


def svg_to_pil(svg_text: str, scale: float = 1.0) -> Image.Image:
    """Convert SVG text to a PIL Image."""
    png_data = cairosvg.svg2png(bytestring=svg_text.encode("utf-8"), scale=scale)
    return Image.open(io.BytesIO(png_data))


async def capture_frames() -> list[Image.Image]:
    """Drive the TUI and capture screenshots at key moments."""
    frames: list[Image.Image] = []

    with patch("krayne.tui.screens.cluster_list.list_clusters", return_value=CLUSTERS):
        app = IKrayneApp()
        async with app.run_test(size=(100, 30)) as pilot:
            # Frame 1: Cluster list
            for _ in range(5):
                await pilot.pause()
            svg = app.export_screenshot()
            frames.append(svg_to_pil(svg))

            # Frame 2: Open create form
            await pilot.press("c")
            for _ in range(3):
                await pilot.pause()
            svg = app.export_screenshot()
            frames.append(svg_to_pil(svg))

            # Frame 3: Go back to list
            await pilot.press("escape")
            for _ in range(3):
                await pilot.pause()
            svg = app.export_screenshot()
            frames.append(svg_to_pil(svg))

            # Frame 4: Open detail screen
            with patch(
                "krayne.tui.screens.cluster_detail.describe_cluster",
                return_value=DETAILS,
            ), patch(
                "krayne.tui.screens.cluster_detail.is_tunnel_active",
                return_value=False,
            ):
                table = app.screen.query_one("DataTable")
                table.focus()
                await pilot.pause()
                await pilot.press("enter")
                for _ in range(5):
                    await pilot.pause()
                svg = app.export_screenshot()
                frames.append(svg_to_pil(svg))

            # Frame 5: Go back, open help
            await pilot.press("escape")
            for _ in range(3):
                await pilot.pause()
            await pilot.press("question_mark")
            for _ in range(3):
                await pilot.pause()
            svg = app.export_screenshot()
            frames.append(svg_to_pil(svg))

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

    # Create GIF with durations: longer pause on list and detail, shorter on transitions
    durations = [3000, 3000, 1500, 3000, 3000]  # ms per frame
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
