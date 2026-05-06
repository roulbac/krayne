# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
uv sync               # Install all dependencies (dev + runtime)
uv run krayne <command>             # Run the CLI
```

## Testing

```bash
uv run pytest tests/unit                              # Unit tests only
uv run pytest tests/unit/test_manifest.py             # Single test file
uv run pytest tests/unit/test_manifest.py::test_name  # Single test
uv run pytest tests/integration -m integration        # Integration tests (requires kubectl, helm, Docker)
```

Unit test timeout: 60s. Integration test timeout: 600s. CI runs against Python 3.10, 3.11, 3.12.

## Architecture

Krayne is a CLI + Python SDK for managing Ray clusters on Kubernetes via the KubeRay CRD (`ray.io/v1/rayclusters`).

**Dependency flow:**
```
CLI (cli/app.py) → SDK (api/clusters.py) → KubeClient (kube/client.py)
                                          → Config (config/models.py + loader.py)
                                          → Manifest (kube/manifest.py)
CLI → Output (output/formatters.py)
```

**Key design decisions:**
- **Functional-first**: SDK is 7 free functions in `api/clusters.py`, no behavior classes. `KubeClient` is a Protocol (structural typing) passed as a parameter — never imported at call site.
- **Pydantic at boundaries**: `ClusterConfig` (input) uses Pydantic with `extra="forbid"`. Output types (`ClusterInfo`, `ClusterDetails`) are frozen dataclasses.
- **Zero-config**: Every CLI command works with zero flags. `krayne create my-cluster` just works.
- **CLI = thin wrapper**: CLI only does argument parsing + Rich output formatting. All logic lives in `api/clusters.py`.
- **Manifest builder is pure**: `build_manifest(config) -> dict` — no I/O, easy to snapshot-test.

**Error hierarchy**: All exceptions inherit from `KrayneError` in `errors.py`. CLI catches `KrayneError` and renders a Rich panel. SDK functions raise specific subtypes (`ClusterNotFoundError`, `ClusterAlreadyExistsError`, `ConfigValidationError`, `ClusterTimeoutError`, `KubeConnectionError`, `NamespaceNotFoundError`).

**Testing pattern**: Unit tests mock `KubeClient` by passing a `MagicMock` that satisfies the Protocol. The mock returns raw K8s-style dicts (same shape as real API responses). No patching of imports needed — just pass the mock via the `client=` kwarg.

## Docs

Documentation uses MkDocs with `mkdocs-material` theme. Design blueprint is at `docs/DESIGN.md`.

```bash
uv sync
uv run mkdocs serve   # Local preview
```

## Regenerating documentation assets

Two scripts produce the assets referenced in README.md and docs/. Both depend on `playwright` + `pillow` (dev deps) and Chromium (`uv run playwright install chromium`, idempotent).

- `scripts/capture_tui_demo.py` — animated GIF of the TUI. Uses mocked cluster data, no live cluster needed. Outputs `docs/assets/ikrayne-demo.gif` plus `ikrayne-frame-{0..10}.png` debug frames. Run after any TUI change.
- `scripts/capture_cli_screenshots.py` — PNGs of CLI commands. Runs real `krayne create/get/delete` etc., so requires a working sandbox (`krayne sandbox setup`). Outputs ~9 PNGs to `docs/assets/`. Run after CLI output changes.

```bash
uv sync
uv run playwright install chromium
uv run python scripts/capture_tui_demo.py
uv run python scripts/capture_cli_screenshots.py   # only if CLI output changed
```

The TUI script imports `IKrayneApp` from `src/krayne/tui/app.py` and drives it via `app.run_test()` + `pilot.press()`. Frames are captured via `app.export_screenshot()` (SVG → PNG via Playwright → GIF via Pillow). Visually inspect the GIF before committing — TUI crashes surface as garbled frames.
