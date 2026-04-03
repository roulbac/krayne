# Python SDK Reference

The Prism SDK is a set of stateless functions for managing Ray clusters programmatically. All functions live in the `prism.api` module.

```python
from prism.api import (
    create_cluster,
    get_cluster,
    list_clusters,
    describe_cluster,
    scale_cluster,
    delete_cluster,
    managed_cluster,
    wait_until_ready,
)
```

## Design principles

- **Functional** — free functions, not classes. No internal state to manage.
- **Explicit dependencies** — the `client` parameter makes the Kubernetes dependency visible and injectable.
- **Immutable returns** — all return types are frozen dataclasses.
- **Testable** — pass a mock `KubeClient` to test without a real cluster.

---

## Functions

### `create_cluster`

Create a new Ray cluster from a configuration object.

```python
def create_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    wait: bool = False,
    timeout: int = 300,
) -> ClusterInfo
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config` | `ClusterConfig` | — | Cluster configuration (required) |
| `client` | `KubeClient \| None` | `None` | Kubernetes client. Uses default kubeconfig if `None`. |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |
| `wait` | `bool` | `False` | Block until the cluster is ready |
| `timeout` | `int` | `300` | Timeout in seconds when `wait=True` |

**Returns:** [`ClusterInfo`](#clusterinfo)

**Raises:**

- `ClusterAlreadyExistsError` — cluster name already in use
- `NamespaceNotFoundError` — namespace does not exist
- `ClusterTimeoutError` — cluster not ready within timeout (when `wait=True`)
- `KubeConnectionError` — cannot reach the Kubernetes API

**Example:**

```python
from prism.api import create_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

config = ClusterConfig(
    name="training-run",
    namespace="ml-team",
    worker_groups=[
        WorkerGroupConfig(replicas=4, gpus=1, gpu_type="a100")
    ],
)
info = create_cluster(config, wait=True, timeout=600)
print(f"Dashboard: {info.dashboard_url}")
```

---

### `get_cluster`

Retrieve summary information for a single cluster.

```python
def get_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Returns:** [`ClusterInfo`](#clusterinfo)

**Raises:** `ClusterNotFoundError`, `KubeConnectionError`

---

### `list_clusters`

List all Ray clusters in a namespace.

```python
def list_clusters(
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[ClusterInfo]
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Returns:** `list[ClusterInfo]`

**Raises:** `KubeConnectionError`

---

### `describe_cluster`

Get extended details for a cluster, including head node and worker group resource breakdowns.

```python
def describe_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterDetails
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Returns:** [`ClusterDetails`](#clusterdetails)

**Raises:** `ClusterNotFoundError`, `KubeConnectionError`

---

### `scale_cluster`

Scale a worker group to a target replica count.

```python
def scale_cluster(
    name: str,
    namespace: str,
    worker_group: str,
    replicas: int,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> ClusterInfo
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | — | Kubernetes namespace (required) |
| `worker_group` | `str` | — | Name of the worker group to scale (required) |
| `replicas` | `int` | — | Target replica count (required) |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Returns:** [`ClusterInfo`](#clusterinfo)

**Raises:** `PrismError` (worker group not found), `ClusterNotFoundError`, `KubeConnectionError`

---

### `delete_cluster`

Delete a Ray cluster.

```python
def delete_cluster(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> None
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Raises:** `ClusterNotFoundError`, `KubeConnectionError`

---

### `managed_cluster`

Context manager that creates a cluster, waits for readiness, optionally opens tunnels, and cleans up everything on exit.

```python
def managed_cluster(
    config: ClusterConfig,
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    timeout: int = 300,
    tunnel: bool = True,
) -> ContextManager[ManagedClusterResult]
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `config` | `ClusterConfig` | — | Cluster configuration (required) |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |
| `timeout` | `int` | `300` | Timeout in seconds for cluster readiness |
| `tunnel` | `bool` | `True` | Open port-forward tunnels to cluster services |

**Yields:** [`ManagedClusterResult`](#managedclusterresult) once the cluster is ready

**Raises:**

- `ClusterAlreadyExistsError` — cluster name already in use
- `NamespaceNotFoundError` — namespace does not exist
- `ClusterTimeoutError` — cluster not ready within timeout
- `KubeConnectionError` — cannot reach the Kubernetes API

The cluster is always deleted on exit, even if an exception occurs inside the `with` block. When `tunnel=True`, tunnels are closed before the cluster is deleted.

**Example:**

```python
import ray
from prism.api import managed_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

config = ClusterConfig(
    name="experiment",
    worker_groups=[WorkerGroupConfig(replicas=2, gpus=1, gpu_type="a100")],
)

# Tunnels are opened by default — URLs resolve to localhost
with managed_cluster(config, timeout=600) as result:
    ray.init(result.client_url)        # ray://localhost:...
    print(result.dashboard_url)        # http://localhost:...
    # ... run distributed work ...
    ray.shutdown()
# Tunnels closed, then cluster deleted

# Use tunnel=False for in-cluster access (e.g. running inside the same K8s cluster)
with managed_cluster(config, tunnel=False) as result:
    ray.init(result.client_url)        # ray://10.0.0.1:10001
```

---

### `wait_until_ready`

Poll a cluster until it reaches the `ready` state or the timeout expires.

```python
def wait_until_ready(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
    timeout: int = 300,
    _poll_interval: float = 2.0,
) -> ClusterInfo
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |
| `timeout` | `int` | `300` | Maximum seconds to wait |

**Returns:** [`ClusterInfo`](#clusterinfo) once the cluster is ready

**Raises:** `ClusterTimeoutError`, `ClusterNotFoundError`, `KubeConnectionError`

---

### `get_cluster_services()`

Return the list of service names exposed on the cluster head node (e.g. `["dashboard", "client", "notebook", "ssh"]`).

```python
get_cluster_services(
    name: str,
    namespace: str = "default",
    *,
    client: KubeClient | None = None,
    kubeconfig: str | None = None,
) -> list[str]
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | — | Cluster name (required) |
| `namespace` | `str` | `"default"` | Kubernetes namespace |
| `client` | `KubeClient \| None` | `None` | Kubernetes client |
| `kubeconfig` | `str \| None` | `None` | Path to kubeconfig file |

**Returns:** List of service name strings

**Raises:** `ClusterNotFoundError`, `KubeConnectionError`

---

## Return types

All return types are immutable frozen dataclasses defined in `prism.api.types`.

### `ClusterInfo`

Summary information about a Ray cluster.

```python
@dataclass(frozen=True)
class ClusterInfo:
    name: str                    # Cluster name
    namespace: str               # Kubernetes namespace
    status: str                  # Cluster status (e.g. "ready", "creating")
    head_ip: str | None          # Head node pod/service IP
    dashboard_url: str | None    # Ray dashboard URL
    client_url: str | None       # Ray client URL
    notebook_url: str | None     # Jupyter notebook URL (if enabled)
    code_server_url: str | None  # Code Server URL (if enabled)
    ssh_url: str | None          # SSH URL (if enabled)
    num_workers: int             # Total worker replicas
    created_at: str              # Creation timestamp
```

### `ClusterDetails`

Extended cluster information with resource breakdown.

```python
@dataclass(frozen=True)
class ClusterDetails:
    info: ClusterInfo                    # Summary info
    head: HeadNodeInfo                   # Head node resources
    worker_groups: list[WorkerGroupInfo] # Worker group details
    ray_version: str                     # Ray version
    python_version: str                  # Python version
```

### `HeadNodeInfo`

Head node resource details.

```python
@dataclass(frozen=True)
class HeadNodeInfo:
    cpus: str       # CPU count
    memory: str     # Memory (e.g. "48Gi")
    gpus: int       # GPU count
    image: str      # Container image
```

### `WorkerGroupInfo`

Worker group resource details.

```python
@dataclass(frozen=True)
class WorkerGroupInfo:
    name: str            # Worker group name
    replicas: int        # Number of replicas
    cpus: str            # CPUs per worker
    memory: str          # Memory per worker
    gpus: int            # GPUs per worker
    gpu_type: str | None # GPU accelerator type
```

### `ManagedClusterResult`

Aggregated result from `managed_cluster`, combining cluster info with an optional tunnel session.

```python
@dataclass(frozen=True)
class ManagedClusterResult:
    cluster: ClusterInfo              # Cluster information
    tunnel_session: TunnelSession | None  # Tunnel session (None if tunnel=False)
```

URL properties (`dashboard_url`, `client_url`, `notebook_url`, `code_server_url`, `ssh_url`) delegate to `tunnel_session` when available, falling back to `cluster` URLs. Passthrough properties `name`, `namespace`, and `status` delegate to `cluster`.

**Accessing URLs:**

```python
with managed_cluster(config) as result:
    # Top-level properties return tunnel (localhost) URLs when tunnel=True
    result.client_url              # ray://localhost:12346
    result.dashboard_url           # http://localhost:12345

    # Access in-cluster IPs directly via result.cluster
    result.cluster.client_url      # ray://10.0.0.1:10001
    result.cluster.dashboard_url   # http://10.0.0.1:8265
```

### `TunnelSession`

Active tunnel session with local URLs for all forwarded services.

```python
@dataclass(frozen=True)
class TunnelSession:
    cluster_name: str          # Cluster name
    namespace: str             # Kubernetes namespace
    tunnels: list[TunnelInfo]  # List of active tunnels
```

Provides URL properties: `dashboard_url`, `client_url`, `notebook_url`, `code_server_url`, `ssh_url` — each returns the local URL for the corresponding service, or `None` if not tunneled.

---

## KubeClient protocol

The `KubeClient` protocol defines the interface for Kubernetes operations. You can implement it for testing or custom backends.

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class KubeClient(Protocol):
    def create_ray_cluster(self, manifest: dict) -> dict: ...
    def get_ray_cluster(self, name: str, namespace: str) -> dict: ...
    def list_ray_clusters(self, namespace: str) -> list[dict]: ...
    def patch_ray_cluster(self, name: str, namespace: str, patch: dict) -> dict: ...
    def delete_ray_cluster(self, name: str, namespace: str) -> None: ...
    def get_cluster_status(self, name: str, namespace: str) -> str: ...
    def list_pods(self, cluster_name: str, namespace: str) -> list[dict]: ...
    def get_head_node_port(self, cluster_name: str, namespace: str, port_name: str) -> int | None: ...
```

Any object that implements these methods satisfies the protocol — no inheritance required.

**Example mock for testing:**

```python
from unittest.mock import MagicMock
from prism.api import create_cluster
from prism.config import ClusterConfig

mock_client = MagicMock()
mock_client.create_ray_cluster.return_value = {
    "metadata": {"name": "test", "namespace": "default", "creationTimestamp": "now"},
    "spec": {"workerGroupSpecs": [{"replicas": 1}]},
    "status": {"state": "ready", "head": {"podIP": "10.0.0.1"}},
}

config = ClusterConfig(name="test")
info = create_cluster(config, client=mock_client)
assert info.name == "test"
assert info.status == "ready"
```
