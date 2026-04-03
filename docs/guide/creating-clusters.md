# Creating Clusters

This guide covers all the ways to create Ray clusters with Prism — from a single command to complex multi-worker GPU configurations.

---

## Basic creation

The simplest way to create a cluster:

```bash
prism create my-cluster
```

This returns immediately after submitting the cluster to Kubernetes. To wait until it's fully ready:

```bash
prism create my-cluster --wait
```

```title="Terminal output"
╭─ Cluster Created ────────────────────────╮
│  Name:         my-cluster                │
│  Namespace:    default                   │
│  Status:       ready                     │
│  Dashboard:    http://10.0.0.1:8265      │
│  Workers:      1                         │
╰──────────────────────────────────────────╯
```

The default cluster includes:

- **Head node**: 15 CPUs, 48 Gi memory, no GPUs
- **1 worker**: 15 CPUs, 48 Gi memory, no GPUs
- **Services**: Jupyter notebook + SSH enabled

---

## GPU clusters

Add GPUs to workers with CLI flags:

```bash
prism create gpu-experiment \
    --gpus-per-worker 1 \
    --worker-gpu-type a100 \
    --workers 2 \
    --wait
```

This creates 2 workers, each with 1 NVIDIA A100 GPU. Prism sets the appropriate Kubernetes node selectors and resource limits automatically.

!!! note "GPU types"
    Common GPU types: `t4`, `a100`, `v100`, `l4`, `h100`. The value maps to the `cloud.google.com/gke-accelerator` node selector.

---

## Custom resources

Override head and worker resources:

```bash
prism create my-cluster \
    --cpus-in-head 8 \
    --memory-in-head 32Gi \
    --workers 4 \
    --wait
```

---

## Using a YAML config file

For complex configurations — multiple worker groups, custom images, specific services — use a YAML file:

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
  code_server: true
```

```bash
prism create my-experiment --file cluster.yaml --wait
```

!!! tip "CLI flags override YAML"
    When both a YAML file and CLI flags are provided, CLI flags take precedence:

    ```bash
    # YAML sets workers to 4, but this creates 8
    prism create my-experiment --file cluster.yaml --workers 8
    ```

---

## Using the Python SDK

The SDK provides the same functionality for use in scripts, notebooks, and pipelines:

```python
from prism.api import create_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

# Simple cluster
config = ClusterConfig(name="sdk-cluster")
info = create_cluster(config, wait=True)
print(f"Dashboard: {info.dashboard_url}")

# GPU cluster with multiple worker groups
config = ClusterConfig(
    name="training-run",
    namespace="ml-team",
    worker_groups=[
        WorkerGroupConfig(name="cpu-workers", replicas=4),
        WorkerGroupConfig(
            name="gpu-workers",
            replicas=2,
            gpus=1,
            gpu_type="a100",
        ),
    ],
)
info = create_cluster(config, wait=True, timeout=600)
```

### Managed cluster (automatic cleanup)

Use `managed_cluster` as a context manager to create a cluster that is automatically deleted when you're done. By default, port-forward tunnels are opened so all service URLs resolve to `localhost`:

```python
import ray
from prism.api import managed_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

config = ClusterConfig(
    name="experiment",
    worker_groups=[WorkerGroupConfig(replicas=2, gpus=1, gpu_type="a100")],
)

with managed_cluster(config, timeout=600) as result:
    ray.init(result.client_url)        # ray://localhost:...
    print(result.dashboard_url)        # http://localhost:...
    # ... run distributed work ...
    ray.shutdown()
# Tunnels closed, then cluster deleted — even if an exception occurs
```

This is useful for scripts, CI pipelines, and notebooks where you want guaranteed cleanup.

!!! tip "Disabling tunnels"
    If you're running inside the same Kubernetes cluster (e.g. in a pod or notebook on the cluster), you can skip tunnels and use in-cluster IPs directly:

    ```python
    with managed_cluster(config, tunnel=False) as result:
        ray.init(result.client_url)    # ray://10.0.0.1:10001
    ```

### Loading from YAML

```python
from prism.api import create_cluster
from prism.config import load_config_from_yaml

# Basic load
config = load_config_from_yaml("cluster.yaml")

# With overrides (supports dot-notation for nested fields)
config = load_config_from_yaml(
    "cluster.yaml",
    overrides={"namespace": "staging", "head.cpus": 32},
)

info = create_cluster(config, wait=True)
```

---

## Specifying a namespace

By default, clusters are created in the `default` namespace:

```bash
# Specify a namespace
prism create my-cluster -n ml-team --wait
```

```python
config = ClusterConfig(name="my-cluster", namespace="ml-team")
```

!!! warning
    The namespace must already exist in Kubernetes. Prism raises `NamespaceNotFoundError` if it doesn't.

---

## Timeout control

The `--wait` flag blocks until the cluster is ready. Control the timeout with `--timeout`:

```bash
# Wait up to 10 minutes
prism create my-cluster --wait --timeout 600
```

```python
info = create_cluster(config, wait=True, timeout=600)
```

If the cluster isn't ready within the timeout, Prism raises `ClusterTimeoutError`.

---

## What's next

- [Managing Clusters](managing-clusters.md) — list, describe, scale, and delete clusters
- [Configuration](configuration.md) — full config model, defaults, and YAML schema
- [CLI Reference](../reference/cli.md) — complete `prism create` flag documentation
