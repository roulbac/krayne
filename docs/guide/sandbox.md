# Local Sandbox

Prism includes a built-in **sandbox** — a local [k3s](https://k3s.io/) Kubernetes cluster running in Docker with the KubeRay operator pre-installed. This lets you develop and test without an external Kubernetes cluster.

---

## Prerequisites

- **Docker** installed and running
- At least **2 CPUs** and **4 GB RAM** allocated to Docker

!!! tip "Check Docker resources"
    On Docker Desktop, go to **Settings → Resources** to verify CPU and memory allocation.

---

## Setting up the sandbox

```bash
prism sandbox setup
```

This runs through 7 automated steps:

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

What happens behind the scenes:

```mermaid
graph LR
  Docker["Docker"] --> K3S["k3s Container<br/>(rancher/k3s)"]
  K3S --> Helm["Helm Install<br/>KubeRay Operator"]
  Helm --> CRD["RayCluster CRD<br/>Registered"]
  CRD --> Ready["Sandbox Ready"]
```

1. Validates Docker is available with sufficient resources
2. Creates a k3s container (`prism-sandbox`)
3. Waits for the k3s node to be ready
4. Extracts kubeconfig to `~/.prism/sandbox-kubeconfig`
5. Installs the KubeRay Helm chart
6. Waits for the `RayCluster` CRD to be registered
7. Waits for the KubeRay operator deployment to be ready

Prism automatically saves the sandbox kubeconfig path and context to `~/.prism/config.yaml`, so all subsequent commands use the sandbox cluster.

---

## Checking sandbox status

```bash
prism sandbox status
```

```title="Terminal output"
╭─ Sandbox Status ────────────────────────────────╮
│  Running:      Yes                              │
│  Container:    prism-sandbox                    │
│  K3S Version:  v1.35.2+k3s1                    │
│  Kubeconfig:   ~/.prism/sandbox-kubeconfig      │
│  Created:      2026-04-01 10:00:00              │
╰─────────────────────────────────────────────────╯
```

---

## Using the sandbox

Once the sandbox is running, all Prism commands work as normal:

```bash
# Create a cluster in the sandbox
prism create my-cluster --wait

# List clusters
prism get

# Describe a cluster
prism describe my-cluster

# Clean up
prism delete my-cluster --force
```

### Sandbox URL rewriting

In a sandbox environment, Prism automatically rewrites cluster URLs to use `localhost` with NodePort mappings. This means dashboard and client URLs work directly from your machine:

| URL Type | Standard Cluster | Sandbox |
|---|---|---|
| Dashboard | `http://<pod-ip>:8265` | `http://localhost:<nodeport>` |
| Ray Client | `ray://<pod-ip>:10001` | `ray://localhost:<nodeport>` |

---

## Tearing down the sandbox

```bash
prism sandbox teardown
```

This removes the Docker container, deletes the sandbox kubeconfig file, and clears Prism settings if they point to the sandbox.

!!! warning
    Teardown is permanent. All clusters and data in the sandbox are lost.

---

## What's next

- [Creating Clusters](creating-clusters.md) — create your first cluster
- [Quickstart](quickstart.md) — end-to-end walkthrough
