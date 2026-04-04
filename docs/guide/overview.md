# Overview

**CLI and SDK for creating, managing, and scaling Ray clusters on Kubernetes.**

Krayne wraps the [KubeRay](https://ray-project.github.io/kuberay/) operator behind a clean, opinionated interface so ML practitioners can get distributed compute without touching Kubernetes manifests.

---

## Why Krayne?

Running Ray on Kubernetes typically requires writing verbose YAML manifests for the `RayCluster` custom resource, understanding Kubernetes CRDs, pod specs, resource requests, and node selectors, stitching together `kubectl` commands for lifecycle management, and manually configuring services like dashboards, notebooks, and SSH. This is a significant barrier for ML practitioners who just want distributed compute.

Krayne eliminates that friction:

- **One command to a working cluster** — `krayne create my-cluster` gives you a Ray cluster with notebooks and SSH ready to go.
- **SDK for automation** — the same operations are available as Python functions for pipelines, scripts, and notebooks.
- **No Kubernetes knowledge required** — sensible defaults handle resource allocation, service configuration, and manifest generation.
- **Full escape hatch** — power users can override any setting via YAML or drop down to raw KubeRay manifests.

---

## How it works

```mermaid
graph LR
  User["You"] -->|"CLI or Python"| Krayne["Krayne"]
  Krayne -->|"builds manifest"| KubeRay["KubeRay CRD"]
  KubeRay -->|"reconciles"| Ray["Ray Cluster"]
  Ray -->|"serves"| Services["Dashboard\nNotebook\nSSH"]
```

Both the CLI and SDK produce the same result — a fully configured Ray cluster with dashboard, notebook, and SSH access:

=== "CLI"

    ```bash
    # Create a GPU cluster with 2 workers
    krayne create my-experiment --gpus-per-worker 1 --workers 2

    # Check status
    krayne describe my-experiment

    # Scale up
    krayne scale my-experiment --replicas 4

    # Clean up
    krayne delete my-experiment --force
    ```

=== "Python SDK"

    ```python
    from krayne.api import create_cluster, scale_cluster, delete_cluster
    from krayne.config import ClusterConfig, WorkerGroupConfig

    config = ClusterConfig(
        name="my-experiment",
        namespace="ml-team",
        worker_groups=[
            WorkerGroupConfig(replicas=2, gpus=1, gpu_type="a100")
        ],
    )

    # Create and wait for ready
    info = create_cluster(config, wait=True)
    print(f"Dashboard: {info.dashboard_url}")

    # Scale up
    scale_cluster("my-experiment", "ml-team", "worker", replicas=4)

    # Clean up
    delete_cluster("my-experiment", "ml-team")
    ```

---

## At a glance

| Feature | Details |
|---|---|
| **Language** | Python 3.10+ |
| **CLI framework** | [Typer](https://typer.tiangolo.com/) + [Rich](https://rich.readthedocs.io/) |
| **Config validation** | [Pydantic v2](https://docs.pydantic.dev/) |
| **K8s integration** | [kubernetes-client](https://github.com/kubernetes-client/python) |
| **CRD target** | KubeRay `RayCluster` (`ray.io/v1`) |
| **Architecture** | Functional-first, stateless SDK |
| **License** | Apache 2.0 |

---

## Architecture

```mermaid
graph TD
  CLI["<b>CLI</b><br/>Typer + Rich"]
  SDK["<b>SDK</b><br/>Functional API"]
  Config["<b>Config</b><br/>Pydantic models"]
  Kube["<b>KubeClient</b><br/>Protocol + manifest"]
  Output["<b>Output</b><br/>Rich formatters"]
  K8s["<b>Kubernetes API</b>"]

  CLI --> SDK
  CLI --> Output
  SDK --> Config
  SDK --> Kube
  Kube --> K8s
```

| Module | Responsibility |
|---|---|
| **CLI** (`krayne.cli`) | Parse arguments, call SDK, format output |
| **SDK** (`krayne.api`) | All business logic as free functions |
| **Config** (`krayne.config`) | Pydantic models + YAML loading |
| **KubeClient** (`krayne.kube`) | Kubernetes API calls + manifest building |
| **Output** (`krayne.output`) | Rich tables and panels for terminal display |

The CLI is a thin wrapper — every operation available from the command line is available as a Python function with the same semantics.

---

## Key features

| Feature | Description |
|---|---|
| **Zero-config defaults** | `krayne create my-cluster` just works — sensible CPU, memory, and service defaults |
| **GPU support** | One flag to add GPUs: `--gpus-per-worker 1 --worker-gpu-type a100` |
| **YAML configuration** | Full cluster spec in a YAML file for version control and reproducibility |
| **Local sandbox** | `krayne sandbox setup` spins up a local k3s cluster with KubeRay for development |
| **JSON output** | Every command supports `--output json` for scripting and pipelines |
| **Functional SDK** | Stateless free functions — no classes to instantiate, no state to manage |
| **Testable by design** | `KubeClient` Protocol enables mock injection without patching imports |

---

## What's next

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } **Quickstart**

    ---

    Install Krayne and create your first cluster in under 5 minutes.

    [:octicons-arrow-right-24: Quickstart](quickstart.md)

-   :material-book-open-variant:{ .lg .middle } **Core Concepts**

    ---

    Understand Ray clusters, KubeRay, and the cluster lifecycle.

    [:octicons-arrow-right-24: Core Concepts](core-concepts.md)

-   :material-console:{ .lg .middle } **CLI Reference**

    ---

    Full reference for every `krayne` command, flag, and option.

    [:octicons-arrow-right-24: CLI Reference](../reference/cli.md)

-   :material-language-python:{ .lg .middle } **Python SDK**

    ---

    Use Krayne programmatically in scripts, notebooks, and pipelines.

    [:octicons-arrow-right-24: SDK Reference](../reference/sdk.md)

</div>
