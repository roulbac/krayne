# Quickstart

Get from zero to a running Ray cluster in under 5 minutes.

---

## Prerequisites

- **Python 3.10+**
- **One of:**
    - A Kubernetes cluster with the [KubeRay operator](https://ray-project.github.io/kuberay/) installed
    - Docker (for the local sandbox — no existing cluster needed)

## 1. Install Krayne

```bash
pip install krayne
```

Verify the installation:

```bash
$ krayne --version
krayne 0.1.0
```

## 2. Connect to a cluster

Choose one of two paths:

=== "Local Sandbox (no cluster needed)"

    Spin up a local k3s cluster with KubeRay pre-installed:

    ```bash
    krayne sandbox setup
    ```

    ```title="Terminal output"
              Sandbox Setup
      Component             Status
      Docker                ✓ ready
      K3S Container         ✓ ready
      K3S Node              ✓ ready
      Kubeconfig            ✓ ready
      KubeRay Helm Chart    ✓ ready
      RayCluster CRD        ✓ ready
      Operator Ready        ✓ ready
    ╭─ Sandbox Ready ─────────────────────────────────╮
    │  Status        running                          │
    │  Kubeconfig    ~/.krayne/sandbox-kubeconfig       │
    ╰─────────────────────────────────────────────────╯
    ╭─ Next Steps ────────────────────────────────────╮
    │  1.  krayne init — select the sandbox            │
    │      kubeconfig and context                     │
    │  2.  krayne create my-cluster — launch your      │
    │      first Ray cluster                          │
    ╰─────────────────────────────────────────────────╯
    ```

    The sandbox requires Docker with at least 2 CPUs and 6 GB RAM.

    Then run `krayne init` to select the sandbox kubeconfig:

    ```bash
    krayne init
    ```

    Select **"Sandbox kubeconfig"** when prompted. Krayne auto-selects the `default` context:

    ```title="Terminal output"
    ? Select kubeconfig source: Sandbox kubeconfig (~/.krayne/sandbox-kubeconfig)
    Auto-selected context: default
    ╭─ Krayne Initialized ────────────────────────────╮
    │  Kubeconfig    ~/.krayne/sandbox-kubeconfig      │
    │  Context       default                          │
    ╰─────────────────────────────────────────────────╯
    ```

=== "Existing Kubernetes Cluster"

    Point Krayne at your kubeconfig:

    ```bash
    krayne init
    ```

    This interactively selects your kubeconfig file and Kubernetes context, then saves the settings to `~/.krayne/config.yaml`.

    !!! tip "Non-interactive mode"
        ```bash
        krayne init --kubeconfig ~/.kube/config --context my-context
        ```

## 3. Create your first cluster

```bash
krayne create my-first-cluster --wait
```

The `--wait` flag blocks until all pods are running:

![krayne create output](../assets/cli-create.png)

!!! tip "Local access"
    To access cluster services from your machine, use `krayne tun-open my-first-cluster` to create localhost port-forwards. Use `krayne tun-close my-first-cluster` to stop.

This creates a cluster with sensible defaults:

- **Head node**: 15 CPUs, 48 Gi memory
- **1 worker**: 15 CPUs, 48 Gi memory
- **Services**: Jupyter notebook + SSH enabled

## 4. Check your cluster

List all clusters:

```bash
$ krayne get
```

![krayne get output](../assets/cli-get.png)

Get detailed information:

```bash
$ krayne describe my-first-cluster
```

![krayne describe output](../assets/cli-describe.png)

## 5. Clean up

```bash
krayne delete my-first-cluster --force
```

If you used the sandbox, you can tear it down:

```bash
krayne sandbox teardown
```

---

## What's next

- [Core Concepts](core-concepts.md) — understand Ray clusters, head/worker nodes, and the lifecycle
- [Creating Clusters](creating-clusters.md) — GPU clusters, YAML configs, and the Python SDK
- [Managing Clusters](managing-clusters.md) — scaling, describing, and deleting clusters
- [Configuration](configuration.md) — config sources, defaults, and YAML schema
