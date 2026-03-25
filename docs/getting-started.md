# Getting Started

This guide walks you through installing Prism and creating your first Ray cluster on Kubernetes.

## Prerequisites

Before you begin, make sure you have:

- **Python 3.10+**
- **A Kubernetes cluster** with the [KubeRay operator](https://ray-project.github.io/kuberay/) installed
- **A valid kubeconfig** — either a local `~/.kube/config` or running inside the cluster

!!! tip "Don't have a cluster yet?"
    You can use [kind](https://kind.sigs.k8s.io/) or [minikube](https://minikube.sigs.k8s.io/) for local development. Install the KubeRay operator with:

    ```bash
    helm repo add kuberay https://ray-project.github.io/kuberay-helm/
    helm install kuberay-operator kuberay/kuberay-operator
    ```

## Installation

Install Prism from PyPI:

```bash
pip install prism-sdk
```

Verify the installation:

```bash
prism --version
```

## Create your first cluster

The simplest way to create a Ray cluster:

```bash
prism create my-first-cluster
```

This creates a cluster with sensible defaults:

- **Head node**: 15 CPUs, 48Gi memory
- **1 worker**: 15 CPUs, 48Gi memory
- **Services**: Jupyter notebook + SSH enabled

### Wait for it to be ready

Add `--wait` to block until the cluster is fully running:

```bash
prism create my-first-cluster --wait
```

### Check your cluster

List all clusters in the current namespace:

```bash
prism get
```

Get detailed information about a specific cluster:

```bash
prism describe my-first-cluster
```

## Create a GPU cluster

Create a cluster with GPU workers:

```bash
prism create gpu-experiment \
    --gpus-per-worker 1 \
    --worker-gpu-type a100 \
    --workers 2 \
    --wait
```

## Scale workers

Scale the worker group up or down:

```bash
prism scale my-first-cluster --replicas 4
```

## Delete a cluster

```bash
prism delete my-first-cluster
```

Add `--force` to skip the confirmation prompt.

## Using the Python SDK

Everything available in the CLI is also available as Python functions:

```python
from prism.api import create_cluster, list_clusters, delete_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

# Create a cluster
config = ClusterConfig(
    name="sdk-cluster",
    namespace="default",
    worker_groups=[
        WorkerGroupConfig(replicas=2, gpus=1, gpu_type="a100")
    ],
)
info = create_cluster(config, wait=True)
print(f"Cluster ready! Dashboard: {info.dashboard_url}")

# List clusters
for cluster in list_clusters():
    print(f"  {cluster.name} — {cluster.status}")

# Clean up
delete_cluster("sdk-cluster")
```

## Using a YAML config file

For complex configurations (multiple worker groups, custom images), use a YAML file:

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
services:
  notebook: true
  vscode_server: true
```

```bash
prism create my-experiment --file cluster.yaml --wait
```

CLI flags override values in the YAML file when both are provided.

## JSON output

All commands support `--output json` for scripting and piping:

```bash
prism get --output json | jq '.[].name'
prism describe my-cluster --output json
```

## Next steps

- [CLI Reference](cli.md) — full command and flag documentation
- [Python SDK Reference](sdk.md) — complete function signatures and examples
- [Configuration](configuration.md) — all config fields, defaults, and YAML schema
