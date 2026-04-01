# Design Document

## What is Prism

**Prism** is an open-source Python package that provides a CLI and programmatic SDK for creating, managing, and scaling Ray clusters on Kubernetes. It wraps the KubeRay operator behind a clean, opinionated interface so that ML practitioners can get distributed compute without touching Kubernetes manifests.

---

## Scope

### In Scope

- **CLI (`prism`):** Command-line tool for interactive cluster management, built with Typer and Rich
- **Python SDK (`prism.api`):** Programmatic API that mirrors the CLI
- **Cluster lifecycle:** Create, describe, list, scale, and delete Ray clusters
- **Config model:** Pydantic-based cluster configuration with sensible defaults and YAML override support
- **Developer experience:** Pre-installed notebook server, VS Code server, SSH access

### Out of Scope

- The KubeRay operator itself (Prism consumes it, does not ship it)
- GKE/EKS/AKS cluster provisioning or node pool management
- Workflow orchestration, experiment tracking, observability, model serving

---

## Design Principles

| Principle | Description |
|---|---|
| **Progressive Disclosure** | Every command works with zero flags. Defaults produce a working cluster. |
| **Functional-First** | Free functions over classes. Dataclasses for data. No inheritance hierarchies. |
| **CLI = SDK** | CLI is a thin Typer shell. All logic lives in `api/clusters.py`. |
| **Pydantic at Boundaries** | All config input validated by Pydantic. Return types are plain frozen dataclasses. |
| **Testable Without a Cluster** | `KubeClient` is injected via Protocol, never imported directly. |

---

## Package Structure

See [Architecture](architecture.md) for the full module overview and dependency graph.

| Module | Path | Responsibility |
|---|---|---|
| **CLI** | `prism/cli/` | Typer commands. Parses args, calls SDK, formats output. |
| **SDK** | `prism/api/` | Functional API: `create_cluster()`, `get_cluster()`, etc. |
| **Config** | `prism/config/` | Pydantic models, YAML loading, validation. |
| **K8s Client** | `prism/kube/` | `KubeClient` Protocol, `DefaultKubeClient`, `build_manifest()`. |
| **Output** | `prism/output/` | Rich formatters for CLI display and JSON output. |

---

## Error Handling

All exceptions inherit from `PrismError`. See [Error Handling](reference/errors.md) for the full hierarchy.

The CLI catches `PrismError` and renders a Rich panel. `--debug` shows the full traceback.

---

## Testing Strategy

| Layer | Approach |
|---|---|
| **Config** | Valid/invalid inputs, default merging, YAML round-trips |
| **K8s Client** | Mock kubernetes-client. Snapshot-test `build_manifest()`. |
| **SDK** | Inject mock `KubeClient`. Test lifecycle independently. |
| **CLI** | Typer `CliRunner` tests for output and flag parsing. |
| **Integration** | End-to-end against a real or kind cluster. |
