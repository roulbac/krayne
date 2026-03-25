# Configuration

Prism uses [Pydantic v2](https://docs.pydantic.dev/) models for all cluster configuration. This provides type validation, sensible defaults, and clear error messages for invalid input.

## Configuration sources

Configuration is resolved from three sources, in order of precedence (highest wins):

1. **CLI flags** — individual options like `--gpus-per-worker 1`
2. **YAML file** — full cluster spec via `--file cluster.yaml`
3. **Built-in defaults** — sensible values so zero-config works

## Config models

### `ClusterConfig`

Top-level configuration for a Ray cluster.

```python
from prism.config import ClusterConfig

config = ClusterConfig(
    name="my-cluster",          # required
    namespace="default",        # optional
    head=HeadNodeConfig(...),   # optional — uses defaults
    worker_groups=[...],        # optional — single default worker
    services=ServicesConfig(...),  # optional — uses defaults
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `head` | `HeadNodeConfig` | See below | Head node configuration |
| `worker_groups` | `list[WorkerGroupConfig]` | `[WorkerGroupConfig()]` | Worker group configurations |
| `services` | `ServicesConfig` | See below | Enabled services |

!!! note
    `ClusterConfig` uses `extra = "forbid"` — unknown fields in YAML or keyword arguments raise a validation error.

---

### `HeadNodeConfig`

Resource configuration for the Ray head node.

| Field | Type | Default | Description |
|---|---|---|---|
| `cpus` | `int` | `15` | CPU count |
| `memory` | `str` | `"48Gi"` | Memory allocation |
| `gpus` | `int` | `0` | GPU count (head typically does not need GPUs) |
| `image` | `str \| None` | `None` | Custom container image. Defaults to `rayproject/ray:2.41.0` |

---

### `WorkerGroupConfig`

Configuration for a worker group.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | `"worker"` | Worker group name |
| `replicas` | `int` | `1` | Number of worker replicas |
| `cpus` | `int` | `15` | CPUs per worker |
| `memory` | `str` | `"48Gi"` | Memory per worker |
| `gpus` | `int` | `0` | GPUs per worker |
| `gpu_type` | `str` | `"t4"` | GPU accelerator type (e.g. `t4`, `a100`, `v100`) |
| `image` | `str \| None` | `None` | Custom container image |

---

### `ServicesConfig`

Services to enable on the cluster.

| Field | Type | Default | Description |
|---|---|---|---|
| `notebook` | `bool` | `True` | Jupyter notebook server |
| `vscode_server` | `bool` | `False` | VS Code server |
| `ssh` | `bool` | `True` | SSH access |
| `tutorials` | `bool` | `False` | Bundled tutorial notebooks |

---

## Default values rationale

| Default | Value | Why |
|---|---|---|
| Head CPUs | 15 | Enough for GCS + dashboard + scheduling |
| Head Memory | 48Gi | Comfortable for object store and metadata |
| Head GPUs | 0 | Head should not run GPU workloads |
| Worker Replicas | 1 | Minimal viable cluster; scale up explicitly |
| Worker CPUs | 15 | Matches typical cloud node size |
| Worker Memory | 48Gi | Comfortable for most training workloads |
| Worker GPUs | 0 | CPU-only by default; opt in via flag |
| GPU Type | t4 | Most available, cost-effective default |
| Notebook | enabled | Most users want immediate notebook access |
| VS Code | disabled | Opt-in; not all users need it |

---

## YAML configuration

For complex setups, define your cluster in a YAML file that maps directly to `ClusterConfig`:

```yaml title="cluster.yaml"
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
    image: rayproject/ray:2.41.0-gpu
services:
  notebook: true
  vscode_server: true
  tutorials: true
```

Use it with the CLI:

```bash
prism create my-experiment --file cluster.yaml
```

### Overriding YAML values with CLI flags

CLI flags take precedence over YAML values:

```bash
# YAML sets workers=1, but this creates 4
prism create my-experiment --file cluster.yaml --workers 4
```

### Loading YAML from Python

```python
from prism.config import load_config_from_yaml

# Basic load
config = load_config_from_yaml("cluster.yaml")

# With overrides (supports dot-notation for nested fields)
config = load_config_from_yaml(
    "cluster.yaml",
    overrides={"namespace": "staging", "head.cpus": 32},
)
```

---

## Minimal configuration

The only required field is `name`. Everything else has a default:

=== "CLI"

    ```bash
    prism create my-cluster
    ```

=== "Python"

    ```python
    from prism.config import ClusterConfig
    config = ClusterConfig(name="my-cluster")
    ```

=== "YAML"

    ```yaml
    name: my-cluster
    ```

This creates a cluster with:

- Head: 15 CPUs, 48Gi memory, no GPUs
- 1 worker: 15 CPUs, 48Gi memory, no GPUs
- Jupyter notebook + SSH enabled
