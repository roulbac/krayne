# CLI Reference

Prism provides a command-line interface built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/) for managing Ray clusters on Kubernetes.

## Global options

These options are available on every command:

| Option | Description |
|---|---|
| `--version`, `-V` | Show version and exit |
| `--debug` | Show full tracebacks on error |
| `--output`, `-o` | Output format: `table` (default) or `json` |
| `--kubeconfig` | Path to kubeconfig file |

---

## `prism init`

Initialize Prism with kubeconfig and Kubernetes context. Saves settings to `~/.prism/config.yaml`.

```
prism init [OPTIONS]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `-k`, `--kubeconfig` | — | Path to kubeconfig file (skips interactive prompt) |
| `-c`, `--context` | — | Kubernetes context name (skips interactive prompt) |

**Examples:**

```bash
# Interactive mode — select kubeconfig and context from menus
prism init

# Non-interactive mode
prism init --kubeconfig ~/.kube/config --context my-context
```

```title="Terminal output"
╭─ Prism Initialized ─────────────────────────╮
│  Kubeconfig:   ~/.kube/config               │
│  Context:      my-context                   │
╰─────────────────────────────────────────────╯
```

!!! note
    In interactive mode, Prism presents a menu to select the kubeconfig source (default location, sandbox, or custom path), then lists available contexts.

---

## `prism create`

Create a new Ray cluster.

```
prism create <name> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `name` | Cluster name (required) |

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |
| `--gpus-per-worker` | `0` | Number of GPUs per worker node |
| `--worker-gpu-type` | `t4` | GPU accelerator type (e.g. `t4`, `a100`, `v100`) |
| `--cpus-in-head` | `15` | CPU count for the head node |
| `--memory-in-head` | `48Gi` | Memory for the head node |
| `--workers` | `1` | Number of worker replicas |
| `--wait`, `-w` | `false` | Wait for cluster to be ready before returning |
| `--timeout` | `300` | Timeout in seconds when using `--wait` |
| `--file`, `-f` | — | Path to a YAML config file |

**Examples:**

```bash
# Minimal — all defaults
prism create my-cluster

# GPU cluster with 2 workers
prism create gpu-cluster --gpus-per-worker 1 --worker-gpu-type a100 --workers 2

# From YAML config, wait for ready
prism create my-cluster --file cluster.yaml --wait --timeout 600

# JSON output
prism create my-cluster --output json
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

!!! note
    When using `--file`, the `name` argument and any CLI flags override the corresponding values in the YAML file.

---

## `prism get`

List all Ray clusters in a namespace.

```
prism get [OPTIONS]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |

**Examples:**

```bash
# List clusters in default namespace
prism get

# List clusters in a specific namespace
prism get -n ml-team

# JSON output for scripting
prism get --output json
```

```title="Terminal output"
┌────────────────┬───────────┬─────────┬─────────┬─────────────────────┐
│ Name           │ Namespace │ Status  │ Workers │ Created             │
├────────────────┼───────────┼─────────┼─────────┼─────────────────────┤
│ my-cluster     │ default   │ ready   │ 1       │ 2026-04-01 10:30:00 │
│ gpu-experiment │ default   │ ready   │ 2       │ 2026-04-01 09:15:00 │
└────────────────┴───────────┴─────────┴─────────┴─────────────────────┘
```

---

## `prism describe`

Show detailed information about a cluster, including head node and worker group resource allocations.

```
prism describe <name> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `name` | Cluster name (required) |

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |

**Examples:**

```bash
prism describe my-cluster
prism describe my-cluster -n ml-team --output json
```

```title="Terminal output"
╭─ Cluster: my-cluster ────────────────────╮
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

---

## `prism scale`

Scale a worker group of a cluster to a target replica count.

```
prism scale <name> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `name` | Cluster name (required) |

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |
| `-g`, `--worker-group` | `worker` | Name of the worker group to scale |
| `-r`, `--replicas` | — | Target replica count (required) |

**Examples:**

```bash
# Scale default worker group to 4 replicas
prism scale my-cluster --replicas 4

# Scale a named worker group
prism scale my-cluster --worker-group gpu-workers --replicas 8 -n ml-team
```

```title="Terminal output"
╭─ Cluster Scaled ─────────────────────────╮
│  Name:         my-cluster                │
│  Namespace:    default                   │
│  Status:       ready                     │
│  Workers:      4                         │
╰──────────────────────────────────────────╯
```

---

## `prism delete`

Delete a Ray cluster.

```
prism delete <name> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `name` | Cluster name (required) |

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |
| `--force` | `false` | Skip confirmation prompt |

**Examples:**

```bash
# Interactive confirmation
prism delete my-cluster

# Skip confirmation
prism delete my-cluster --force

# Delete from specific namespace
prism delete my-cluster -n ml-team --force
```

```title="Terminal output"
Are you sure you want to delete cluster 'my-cluster'? [y/N]: y
Cluster 'my-cluster' deleted.
```

---

## `prism sandbox setup`

Set up a local k3s cluster with KubeRay for development.

```
prism sandbox setup
```

Requires Docker with at least 2 CPUs and 4 GB RAM. Creates a k3s container named `prism-sandbox` and installs the KubeRay operator.

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

---

## `prism sandbox teardown`

Tear down the local sandbox cluster.

```
prism sandbox teardown
```

Removes the Docker container, deletes the sandbox kubeconfig, and clears Prism settings if they point to the sandbox.

---

## `prism sandbox status`

Show current status of the sandbox.

```
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

## Output formats

### Table (default)

Rich-formatted tables and panels for human-readable output:

```bash
prism get
prism describe my-cluster
```

### JSON

Machine-readable JSON output, useful for scripting:

```bash
prism get --output json
prism describe my-cluster -o json | jq '.info.status'
```

---

## Error handling

Errors are displayed as Rich panels by default. Use `--debug` to see full Python tracebacks:

```bash
prism describe nonexistent-cluster --debug
```

```title="Terminal output"
╭──── Error ─────────────────────────────────╮
│ Cluster 'nonexistent-cluster' not found in │
│ namespace 'default'                        │
╰────────────────────────────────────────────╯
```

All errors are instances of `PrismError` subclasses. See [Error Types](errors.md) for the full exception hierarchy.
