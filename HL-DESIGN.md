> **High-Level Design Document**

| | |
|---|---|
| **Status** | DRAFT |
| **Version** | 0.1 |
| **Date** | March 2026 |
| **License** | Apache 2.0 (planned) |
| **Repository** | `github.com/<you>/prism` |
| **Language** | Python 3.10+ |
| **Key Deps** | Typer, kubernetes-client, pydantic, rich |

*This document is the implementation blueprint for the Prism Python SDK. It defines the scope, module structure, public API surface, config model, and build order. Use it as the single source of truth when building each module.*

---

## 1. What is Prism

**Prism** is an open-source Python package that provides a CLI and programmatic SDK for creating, managing, and scaling Ray clusters on Kubernetes. It wraps the KubeRay operator behind a clean, opinionated interface so that ML practitioners can get distributed compute without touching Kubernetes manifests.

The name comes from what a prism does to a ray of light: takes a single beam and organizes it into something structured and useful. Prism does the same for Ray clusters.

**In one sentence:** `prism create cluster my-cluster --gpus-per-worker 1` and you have a distributed GPU cluster with notebooks, VS Code, and SSH ready to go.

---

## 2. Scope

### 2.1 In Scope

- **CLI (`prism`):** Command-line tool for interactive cluster management, built with **Typer** and **rich**
- **Python SDK (`prism.api`):** Programmatic API that mirrors the CLI — **functional-first** with dataclasses for data, not class hierarchies for behavior
- **Cluster lifecycle:** Create, describe, list, scale, customize, and delete Ray clusters
- **Config model:** Pydantic-based cluster configuration with sensible defaults and YAML override support
- **Multi-tenancy primitives:** Namespace management, resource quotas, team config files
- **Developer experience:** Pre-installed notebook server, VS Code server, SSH access, bundled tutorials

### 2.2 Out of Scope

- The KubeRay operator itself (Prism consumes it, does not ship it)
- GKE/EKS/AKS cluster provisioning or node pool management
- Workflow orchestration (Flyte, Airflow, etc.)
- ML metadata logging or experiment tracking
- Observability dashboards (cost tracking, platform insights)
- Model serving

*These are potential future extensions but are explicitly not part of v1.*

---

## 3. Design Principles

| Principle | What It Means When Coding |
|---|---|
| **Progressive Disclosure** | Every command works with zero flags. Defaults produce a working cluster. Power users override via flags or YAML. Never force K8s knowledge on the user. |
| **Functional-First** | Prefer free functions over classes for behavior. Use dataclasses/Pydantic models for data. No inheritance hierarchies. State lives in data, not in objects. The SDK is a collection of functions that take config in and return results out. |
| **CLI = SDK** | The CLI is a thin Typer shell over the SDK. All logic lives in the SDK layer. The CLI only parses args, calls a function, and prints the result. |
| **No Magic** | Every abstraction is one layer deep. Users can eject to raw KubeRay YAML at any point. No hidden state, no implicit side effects, no ambient config. |
| **Pydantic Everywhere** | All config, API inputs, and API outputs are Pydantic models or dataclasses. Validation happens at the boundary, not scattered through business logic. |
| **Testable Without a Cluster** | The K8s client is injected, never imported directly. Every module is unit-testable with mocked K8s responses. Integration tests use kind or a test cluster. |
| **Rich Output** | CLI output uses **rich** for tables, panels, and status spinners. JSON output is always available via `--output json` for scripting and piping. |

---

## 4. Package Structure

The package is organized into five modules with clear dependency direction: CLI depends on SDK, SDK depends on Config and K8s Client, nothing depends on CLI.

| Module | Path | Responsibility |
|---|---|---|
| **CLI** | `prism/cli/` | Typer commands. Thin layer: parses args, calls SDK functions, formats output via rich. |
| **SDK** | `prism/api/` | Public Python API. **Functional interface**: `create_cluster()`, `get_cluster()`, `list_clusters()`, `describe_cluster()`, `scale_cluster()`, `delete_cluster()`. All business logic lives here. |
| **Config** | `prism/config/` | Pydantic models (or `@dataclass` where Pydantic is overkill) for cluster spec, worker groups, resource requests. Handles defaults, validation, and YAML serialization. |
| **K8s Client** | `prism/kube/` | Thin wrapper over kubernetes-client. Translates config models into KubeRay CRD manifests. Handles CRUD against the K8s API. Injectable/mockable via a `Protocol`. |
| **Output** | `prism/output/` | Rich formatters for CLI display (tables, panels, status) and machine output (JSON). Shared by all CLI commands. |

**Dependency graph:** `cli/ → api/ → config/ + kube/`. The `output/` module is used only by `cli/`.

---

## 5. Public API Surface

### 5.1 CLI Commands (Typer)

All commands follow the pattern: `prism <verb> <resource> [name] [flags]`

The CLI is built with **Typer** and uses **rich** for all terminal output — tables, panels, progress spinners, and error rendering. Every command supports `--output json` for machine-readable output.

| Command | Description | Key Flags |
|---|---|---|
| `prism create cluster` | Create a new Ray cluster | `-n, --gpus-per-worker, --worker-gpu-type, --cpus-in-head, --memory-in-head, --with-tutorials, --vscode-server, --file` |
| `prism get clusters` | List clusters in a namespace | `-n, --output` |
| `prism describe cluster` | Show detailed cluster info | `-n, --output` |
| `prism scale cluster` | Scale a worker group | `-n, --worker-group, --replicas` |
| `prism delete cluster` | Tear down a cluster | `-n, --force` |

**CLI implementation pattern:**

```python
import typer
from rich.console import Console
from rich.table import Table

from prism.api import create_cluster
from prism.config import ClusterConfig

app = typer.Typer()
console = Console()

@app.command()
def create(
    name: str,
    namespace: str = typer.Option("default", "-n"),
    gpus_per_worker: int = typer.Option(0),
    # ... more flags
):
    """Create a new Ray cluster."""
    config = ClusterConfig(name=name, namespace=namespace, ...)
    result = create_cluster(config)  # SDK function call
    _print_cluster_info(result)      # rich output

def _print_cluster_info(info):
    table = Table(title="Cluster Created")
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Name", info.name)
    table.add_row("Head IP", info.head_ip)
    table.add_row("Dashboard", info.dashboard_url)
    # ...
    console.print(table)
```

### 5.2 Python SDK (Functional API)

The SDK is a set of **free functions**, not a class. Data is represented with **dataclasses** and **Pydantic models**. Behavior lives in functions. State lives in the data they return.

| Function | Signature | Returns |
|---|---|---|
| `create_cluster` | `(config: ClusterConfig, *, client: KubeClient \| None = None, wait: bool = False, timeout: int = 300) -> ClusterInfo` | Cluster metadata after creation |
| `get_cluster` | `(name: str, namespace: str, *, client: KubeClient \| None = None) -> ClusterInfo` | Single cluster info |
| `list_clusters` | `(namespace: str, *, client: KubeClient \| None = None) -> list[ClusterInfo]` | All clusters in namespace |
| `describe_cluster` | `(name: str, namespace: str, *, client: KubeClient \| None = None) -> ClusterDetails` | Extended info with resource breakdown |
| `scale_cluster` | `(name: str, namespace: str, worker_group: str, replicas: int, *, client: KubeClient \| None = None) -> ClusterInfo` | Updated cluster info |
| `delete_cluster` | `(name: str, namespace: str, *, client: KubeClient \| None = None) -> None` | Nothing |
| `wait_until_ready` | `(name: str, namespace: str, *, client: KubeClient \| None = None, timeout: int = 300) -> ClusterInfo` | Cluster info once ready; raises on timeout |

**Why functional over OOP:**

- **Stateless by default.** Each function call is self-contained. No object mutation, no stale internal state.
- **Composable.** Functions can be chained, mapped, and tested independently without instantiating anything.
- **Explicit dependencies.** The `client` parameter makes the K8s dependency visible at every call site. Default `None` means "create a default client from kubeconfig" — explicit, not magical.
- **Easier to test.** No setup/teardown of objects. Pass a mock client, get a result, assert on it.

**SDK usage example:**

```python
from prism.api import create_cluster, scale_cluster, delete_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

# Create a cluster
config = ClusterConfig(
    name="my-experiment",
    namespace="ml-team",
    worker_groups=[
        WorkerGroupConfig(name="gpu-workers", replicas=2, gpus=1, gpu_type="a100")
    ],
)
info = create_cluster(config, wait=True)
print(f"Dashboard: {info.dashboard_url}")

# Scale up
info = scale_cluster("my-experiment", "ml-team", "gpu-workers", replicas=4)

# Clean up
delete_cluster("my-experiment", "ml-team")
```

### 5.3 Return Types (Dataclasses)

The SDK returns plain **dataclasses** (not Pydantic models — these are output data, not validated input):

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class ClusterInfo:
    name: str
    namespace: str
    status: str
    head_ip: str | None
    dashboard_url: str | None
    notebook_url: str | None
    vscode_url: str | None
    num_workers: int
    created_at: str

@dataclass(frozen=True)
class WorkerGroupInfo:
    name: str
    replicas: int
    cpus: int
    memory: str
    gpus: int
    gpu_type: str | None

@dataclass(frozen=True)
class ClusterDetails:
    info: ClusterInfo
    head: HeadNodeInfo
    worker_groups: list[WorkerGroupInfo]
    ray_version: str
    python_version: str
```

`frozen=True` ensures return values are immutable — no accidental mutation.

---

## 6. Configuration Model

All configuration is expressed as **Pydantic models** (input data needs validation; return data does not). Users interact with config in three ways, in order of precedence (highest wins):

1. **CLI flags:** Individual options like `--gpus-per-worker 1`
2. **YAML file:** Full cluster spec via `--file cluster.yaml`
3. **Built-in defaults:** Sensible values so zero-config works

### 6.1 Core Models

```python
from pydantic import BaseModel, Field

class ServicesConfig(BaseModel):
    notebook: bool = True
    vscode_server: bool = False
    ssh: bool = True
    tutorials: bool = False

class HeadNodeConfig(BaseModel):
    cpus: int = 15
    memory: str = "48Gi"
    gpus: int = 0
    image: str | None = None

class WorkerGroupConfig(BaseModel):
    name: str = "worker"
    replicas: int = 1
    cpus: int = 15
    memory: str = "48Gi"
    gpus: int = 0
    gpu_type: str = "t4"
    image: str | None = None

class ClusterConfig(BaseModel):
    name: str
    namespace: str = "default"
    head: HeadNodeConfig = Field(default_factory=HeadNodeConfig)
    worker_groups: list[WorkerGroupConfig] = Field(
        default_factory=lambda: [WorkerGroupConfig()]
    )
    services: ServicesConfig = Field(default_factory=ServicesConfig)
```

### 6.2 Defaults

| Field | Default | Rationale |
|---|---|---|
| Head CPUs | 15 | Enough for GCS + dashboard + scheduling |
| Head Memory | 48Gi | Comfortable for object store and metadata |
| Head GPUs | 0 | Head should not run GPU workloads |
| Worker Replicas | 1 | Minimal viable cluster; scale up explicitly |
| Worker CPUs | 15 | Matches typical cloud node size |
| Worker Memory | 48Gi | Comfortable for most training workloads |
| Worker GPUs | 0 | CPU-only by default; opt in via flag |
| GPU Type | t4 | Most available, cost-effective default |
| Notebook | true | Most users want immediate notebook access |
| VS Code | false | Opt-in; not all users need it |
| Tutorials | false | Opt-in; useful for onboarding |

### 6.3 YAML Override

For advanced use cases (multiple worker groups, custom images, node affinity), users provide a YAML file that maps directly to `ClusterConfig`:

- `prism create cluster my-cluster --file cluster.yaml`
- CLI flags override individual fields in the YAML if both are provided
- Unknown fields in YAML raise a validation error (no silent ignoring)

**Example YAML:**

```yaml
name: my-experiment
namespace: ml-team
head:
  cpus: 8
  memory: 32Gi
worker_groups:
  - name: cpu-workers
    replicas: 4
    cpus: 15
    memory: 48Gi
  - name: gpu-workers
    replicas: 2
    gpus: 1
    gpu_type: a100
services:
  notebook: true
  vscode_server: true
  tutorials: true
```

---

## 7. Kubernetes Client Layer

This is the only module that talks to the Kubernetes API. It has a single job: translate Pydantic config into KubeRay CRD manifests and execute CRUD operations.

### 7.1 Responsibilities

- **Manifest generation:** Convert `ClusterConfig` into a KubeRay RayCluster custom resource dict
- **CRUD operations:** Create, get, list, patch, and delete RayCluster CRs via kubernetes-client
- **Status polling:** Watch cluster status until ready or timeout
- **Namespace resolution:** Validate namespace exists, apply default if not provided

### 7.2 Protocol Interface

The K8s client is defined as a `Protocol` — structural typing, no inheritance required:

```python
from typing import Protocol

class KubeClient(Protocol):
    def create_ray_cluster(self, manifest: dict) -> dict: ...
    def get_ray_cluster(self, name: str, namespace: str) -> dict: ...
    def list_ray_clusters(self, namespace: str) -> list[dict]: ...
    def patch_ray_cluster(self, name: str, namespace: str, patch: dict) -> dict: ...
    def delete_ray_cluster(self, name: str, namespace: str) -> None: ...
    def get_cluster_status(self, name: str, namespace: str) -> str: ...
```

- The SDK creates a default client from kubeconfig when `client=None` is passed
- Tests inject a mock that satisfies this protocol
- No ABC, no registration — any object with the right methods works

### 7.3 CRD Mapping

The manifest generator produces a valid KubeRay RayCluster spec:

| Prism Config Field | KubeRay CRD Path |
|---|---|
| `ClusterConfig.name` | `metadata.name` |
| `ClusterConfig.namespace` | `metadata.namespace` |
| `HeadNodeConfig.cpus` | `spec.headGroupSpec.template.spec.containers[0].resources.requests.cpu` |
| `HeadNodeConfig.memory` | `spec.headGroupSpec.template.spec.containers[0].resources.requests.memory` |
| `WorkerGroupConfig.replicas` | `spec.workerGroupSpecs[n].replicas` |
| `WorkerGroupConfig.gpus` | `spec.workerGroupSpecs[n]...resources.limits[nvidia.com/gpu]` |
| `WorkerGroupConfig.gpu_type` | `spec.workerGroupSpecs[n]...nodeSelector[cloud.google.com/gke-accelerator]` |

The manifest generator is a **pure function**: `def build_manifest(config: ClusterConfig) -> dict` — no side effects, easy to snapshot-test.

---

## 8. Error Handling

Prism defines a small, flat exception hierarchy. All exceptions inherit from `PrismError`:

| Exception | When Raised |
|---|---|
| `ClusterNotFoundError` | get/describe/scale/delete on a nonexistent cluster |
| `ClusterAlreadyExistsError` | create with a name already in use in the namespace |
| `ConfigValidationError` | Invalid config from flags or YAML (wraps Pydantic `ValidationError`) |
| `ClusterTimeoutError` | `wait_until_ready` exceeds timeout |
| `KubeConnectionError` | Cannot reach the Kubernetes API |
| `NamespaceNotFoundError` | Specified namespace does not exist |

The CLI catches all `PrismError` subclasses and renders them as friendly **rich** panels:

```python
from rich.console import Console
from rich.panel import Panel
from prism.errors import PrismError

console = Console(stderr=True)

try:
    result = create_cluster(config)
except PrismError as e:
    console.print(Panel(str(e), title="Error", style="red"))
    raise typer.Exit(1)
```

Unexpected exceptions show a full traceback with `--debug`.

---

## 9. Testing Strategy

| Layer | How to Test | Tools |
|---|---|---|
| **Config** | Unit tests: valid/invalid inputs, default merging, YAML parsing, serialization round-trips | pytest, pydantic |
| **K8s Client** | Unit tests with mocked kubernetes-client. Verify generated manifests match expected CRD structure. `build_manifest()` is pure — snapshot test its output. | pytest, unittest.mock |
| **SDK** | Unit tests with injected mock `KubeClient`. Test full lifecycle: create → describe → scale → delete. Each function tested independently. | pytest, fixtures |
| **CLI** | Snapshot tests using Typer's `CliRunner`. Verify rich output formatting and flag parsing. | pytest, typer.testing.CliRunner |
| **Integration** | End-to-end against kind or a real cluster. Full create/use/scale/delete lifecycle. | pytest, kind, CI |

**Testing the functional API is straightforward:**

```python
from prism.api import create_cluster
from prism.config import ClusterConfig

def test_create_cluster(mock_kube_client):
    config = ClusterConfig(name="test", namespace="default")
    info = create_cluster(config, client=mock_kube_client)
    assert info.name == "test"
    assert info.status == "running"
    mock_kube_client.create_ray_cluster.assert_called_once()
```

---

## 10. Implementation Order

Build bottom-up. Each step depends only on completed steps. Each step should be a self-contained PR with tests.

| # | Module | What to Build | Done When |
|---|---|---|---|
| 1 | **Config models** | `ClusterConfig`, `HeadNodeConfig`, `WorkerGroupConfig`, `ServicesConfig`. Defaults. YAML load/dump. Validation. | All Pydantic models pass unit tests. YAML round-trip works. |
| 2 | **K8s client** | `KubeClient` protocol. Default implementation via kubernetes-client. `build_manifest()` pure function. CRUD wrapper. Status polling. | Generated manifests match KubeRay schema. Mock tests pass. |
| 3 | **SDK (`api/`)** | Functional API: `create_cluster`, `get_cluster`, `list_clusters`, `describe_cluster`, `scale_cluster`, `delete_cluster`, `wait_until_ready`. Exception hierarchy. | Full lifecycle test passes with mocked K8s client. |
| 4 | **Output formatters** | Rich table/panel formatters for cluster info. JSON output mode. Human-readable describe. | Snapshot tests match expected output. |
| 5 | **CLI** | Typer commands: create, get, describe, scale, delete. Wire to SDK functions + rich output. `--debug`, `--output json`. | CliRunner tests pass. All commands work with mocked SDK. |
| 6 | **Integration tests** | E2E tests against kind or real cluster. CI pipeline. | Full lifecycle on real cluster. Green CI. |
| 7 | **Packaging + docs** | `pyproject.toml`, entry points, README, usage examples, GitHub Actions. | `pip install prism-ray` works. README covers quickstart. |

---

## 11. Open Questions

- **PyPI package name:** `prism` is taken. Candidates: `prism-ray`, `rayprism`, `prism-ml`.
- **Async support:** Should the SDK support `asyncio`, or is synchronous + threading sufficient for v1?
- **Minimum KubeRay version:** Pinning to one version simplifies testing but limits adoption. What's the floor?
- **`prism describe` output:** Should it dump full YAML (for debugging), or stay human-readable with a separate `prism export` command?
- **Container images:** Should Prism ship default images or always require user-specified ones?
- **Config file location:** Should Prism support a `~/.prism/config.yaml` for persistent defaults (default namespace, preferred GPU type), or is that too much implicit state for v1?

---

## 12. Future Extensions (Post v1)

Not in scope for v1, but keep these in mind to avoid painting into a corner:

- **Workflow orchestration:** Flyte/Airflow integration for production pipelines
- **Experiment tracking:** Pluggable backends for MLflow, W&B, etc.
- **Observability:** Cost tracking, usage dashboards, health monitoring
- **Autoscaling:** Worker scaling based on Ray task queue depth
- **Warm pools:** Pre-provisioned idle clusters for instant availability
- **Multi-cloud:** EKS and AKS support beyond GKE
- **Web UI:** Browser-based cluster management