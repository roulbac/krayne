# Quickstart

Get from zero to a running Ray cluster in under 5 minutes.

---

## Prerequisites

- **Python 3.10+**
- **One of:**
    - A Kubernetes cluster with the [KubeRay operator](https://ray-project.github.io/kuberay/) installed
    - Docker (for the local sandbox — no existing cluster needed)

## 1. Install Prism

```bash
pip install prism-sdk
```

Verify the installation:

```bash
$ prism --version
prism 0.1.0
```

## 2. Connect to a cluster

Choose one of two paths:

=== "Local Sandbox (no cluster needed)"

    Spin up a local k3s cluster with KubeRay pre-installed:

    ```bash
    prism sandbox setup
    ```

    ```title="Terminal output"
    ┌─────────────────────────────┬───────────────────┐
    │ Step                        │ Status            │
    ├─────────────────────────────┼───────────────────┤
    │ Docker availability         │ ✓ ready           │
    │ K3S container               │ ✓ ready           │
    │ K3S node readiness          │ ✓ ready           │
    │ Kubeconfig extraction       │ ✓ ready           │
    │ KubeRay Helm chart          │ ✓ ready           │
    │ RayCluster CRD              │ ✓ ready           │
    │ KubeRay operator            │ ✓ ready           │
    └─────────────────────────────┴───────────────────┘
    ╭─ Sandbox Ready ─────────────────────────────────╮
    │ Kubeconfig: ~/.prism/sandbox-kubeconfig         │
    ╰─────────────────────────────────────────────────╯
    ```

    The sandbox requires Docker with at least 2 CPUs and 4 GB RAM.

=== "Existing Kubernetes Cluster"

    Point Prism at your kubeconfig:

    ```bash
    prism init
    ```

    This interactively selects your kubeconfig file and Kubernetes context, then saves the settings to `~/.prism/config.yaml`.

    !!! tip "Non-interactive mode"
        ```bash
        prism init --kubeconfig ~/.kube/config --context my-context
        ```

## 3. Create your first cluster

```bash
prism create my-first-cluster --wait
```

The `--wait` flag blocks until all pods are running:

```title="Terminal output"
╭─ Cluster Created ────────────────────────╮
│  Name:         my-first-cluster          │
│  Namespace:    default                   │
│  Status:       ready                     │
│  Dashboard:    http://10.0.0.1:8265      │
│  Workers:      1                         │
╰──────────────────────────────────────────╯
```

This creates a cluster with sensible defaults:

- **Head node**: 15 CPUs, 48 Gi memory
- **1 worker**: 15 CPUs, 48 Gi memory
- **Services**: Jupyter notebook + SSH enabled

## 4. Check your cluster

List all clusters:

```bash
$ prism get
```

```title="Terminal output"
┌──────────────────┬───────────┬─────────┬─────────┬─────────────────────┐
│ Name             │ Namespace │ Status  │ Workers │ Created             │
├──────────────────┼───────────┼─────────┼─────────┼─────────────────────┤
│ my-first-cluster │ default   │ ready   │ 1       │ 2026-04-01 10:30:00 │
└──────────────────┴───────────┴─────────┴─────────┴─────────────────────┘
```

Get detailed information:

```bash
$ prism describe my-first-cluster
```

```title="Terminal output"
╭─ Cluster: my-first-cluster ──────────────╮
│  Namespace:    default                   │
│  Status:       ready                     │
│  Dashboard:    http://10.0.0.1:8265      │
│  Client URL:   ray://10.0.0.1:10001     │
│  Workers:      1                         │
│  Created:      2026-04-01 10:30:00       │
╰──────────────────────────────────────────╯

Head Node
┌──────┬────────┬──────┬─────────────────────────┐
│ CPUs │ Memory │ GPUs │ Image                   │
├──────┼────────┼──────┼─────────────────────────┤
│ 15   │ 48Gi   │ 0    │ rayproject/ray:2.41.0   │
└──────┴────────┴──────┴─────────────────────────┘

Worker Groups
┌─────────┬──────────┬──────┬────────┬──────┬──────────┐
│ Group   │ Replicas │ CPUs │ Memory │ GPUs │ GPU Type │
├─────────┼──────────┼──────┼────────┼──────┼──────────┤
│ worker  │ 1        │ 15   │ 48Gi   │ 0    │ —        │
└─────────┴──────────┴──────┴────────┴──────┴──────────┘
```

## 5. Clean up

```bash
prism delete my-first-cluster --force
```

If you used the sandbox, you can tear it down:

```bash
prism sandbox teardown
```

---

## What's next

- [Core Concepts](core-concepts.md) — understand Ray clusters, head/worker nodes, and the lifecycle
- [Creating Clusters](creating-clusters.md) — GPU clusters, YAML configs, and the Python SDK
- [Managing Clusters](managing-clusters.md) — scaling, describing, and deleting clusters
- [Configuration](configuration.md) — config sources, defaults, and YAML schema
