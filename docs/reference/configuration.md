# Configuration Models

All cluster configuration is defined using [Pydantic v2](https://docs.pydantic.dev/) models. Models are importable from `prism.config`.

```python
from prism.config import (
    ClusterConfig,
    HeadNodeConfig,
    WorkerGroupConfig,
    ServicesConfig,
    load_config_from_yaml,
)
```

---

## `ClusterConfig`

Top-level configuration for a Ray cluster. Uses `extra = "forbid"` — unknown fields raise a validation error.

```python
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

---

## `HeadNodeConfig`

Resource configuration for the Ray head node.

| Field | Type | Default | Description |
|---|---|---|---|
| `cpus` | `str` | `"1"` | CPU count |
| `memory` | `str` | `"2Gi"` | Memory allocation |
| `gpus` | `int` | `0` | GPU count (head typically does not need GPUs) |
| `image` | `str \| None` | `None` | Custom container image. Defaults to `rayproject/ray:latest` |

---

## `WorkerGroupConfig`

Configuration for a worker group.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | `"worker"` | Worker group name |
| `replicas` | `int` | `1` | Number of worker replicas |
| `cpus` | `str` | `"1"` | CPUs per worker |
| `memory` | `str` | `"2Gi"` | Memory per worker |
| `gpus` | `int` | `0` | GPUs per worker |
| `gpu_type` | `str` | `"t4"` | GPU accelerator type (e.g. `t4`, `a100`, `v100`) |
| `image` | `str \| None` | `None` | Custom container image |

---

## `ServicesConfig`

Services to enable on the cluster head node. Each enabled service adds its port to the head pod spec and populates the corresponding URL in `ClusterInfo`.

| Field | Type | Default | Port | Description |
|---|---|---|---|---|
| `notebook` | `bool` | `True` | 8888 | Jupyter notebook server (runs on ray-head container) |
| `vscode_server` | `bool` | `False` | 8080 | VS Code server (runs as a `codercom/code-server` sidecar container) |
| `ssh` | `bool` | `True` | 22 | SSH access to the head node |

The VS Code server image version can be overridden with the `PRISM_VSCODE_VERSION` environment variable.

---

## `load_config_from_yaml`

Load a `ClusterConfig` from a YAML file with optional field overrides.

```python
def load_config_from_yaml(
    path: str | Path,
    overrides: dict[str, Any] | None = None,
) -> ClusterConfig
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `path` | `str \| Path` | — | Path to YAML configuration file (required) |
| `overrides` | `dict[str, Any] \| None` | `None` | Field values taking precedence over YAML. Supports dot-notation keys. |

**Returns:** `ClusterConfig`

**Raises:** `ConfigValidationError` on validation failure

**Example:**

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

## Settings

User-level settings are persisted in `~/.prism/config.yaml` and managed via these functions:

```python
from prism.config import (
    PrismSettings,
    load_prism_settings,
    save_prism_settings,
    clear_prism_settings,
)
```

### `PrismSettings`

```python
@dataclass
class PrismSettings:
    kubeconfig: str | None = None
    kube_context: str | None = None
```

### `load_prism_settings() -> PrismSettings`

Load settings from `~/.prism/config.yaml`, returning defaults if absent.

### `save_prism_settings(settings: PrismSettings) -> None`

Write settings to `~/.prism/config.yaml`, creating the directory if needed.

### `clear_prism_settings() -> None`

Remove the settings file if it exists.
