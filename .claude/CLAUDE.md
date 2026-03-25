# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development

```bash
uv sync               # Install all dependencies (dev + runtime)
uv run prism <command>             # Run the CLI
```

## Testing

```bash
uv run pytest tests/unit                              # Unit tests only
uv run pytest tests/unit/test_manifest.py             # Single test file
uv run pytest tests/unit/test_manifest.py::test_name  # Single test
uv run pytest tests/integration -m integration        # Integration tests (requires k3d, kubectl, helm, Docker)
```

Unit test timeout: 60s. Integration test timeout: 600s. CI runs against Python 3.10, 3.11, 3.12.

## Architecture

Prism is a CLI + Python SDK for managing Ray clusters on Kubernetes via the KubeRay CRD (`ray.io/v1/rayclusters`).

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
- **Zero-config**: Every CLI command works with zero flags. `prism create my-cluster` just works.
- **CLI = thin wrapper**: CLI only does argument parsing + Rich output formatting. All logic lives in `api/clusters.py`.
- **Manifest builder is pure**: `build_manifest(config) -> dict` — no I/O, easy to snapshot-test.

**Error hierarchy**: All exceptions inherit from `PrismError` in `errors.py`. CLI catches `PrismError` and renders a Rich panel. SDK functions raise specific subtypes (`ClusterNotFoundError`, `ClusterAlreadyExistsError`, `ConfigValidationError`, `ClusterTimeoutError`, `KubeConnectionError`, `NamespaceNotFoundError`).

**Testing pattern**: Unit tests mock `KubeClient` by passing a `MagicMock` that satisfies the Protocol. The mock returns raw K8s-style dicts (same shape as real API responses). No patching of imports needed — just pass the mock via the `client=` kwarg.

## Docs

Documentation uses MkDocs with `mkdocs-material` theme. Design blueprint is at `docs/DESIGN.md`.

```bash
uv sync
uv run mkdocs serve   # Local preview
```
