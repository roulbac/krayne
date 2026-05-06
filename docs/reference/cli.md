# CLI Reference

Krayne provides a command-line interface built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/) for managing Ray clusters on Kubernetes.

## Global options

These options are defined on the top-level `krayne` callback and must be passed **before** the subcommand (e.g. `krayne -o json get`, not `krayne get -o json`):

| Option | Description |
|---|---|
| `--version`, `-V` | Show version and exit |
| `--debug` | Show full tracebacks on error |
| `--output`, `-o` | Output format: `table` (default) or `json` |
| `--kubeconfig` | Path to kubeconfig file |

---

## `krayne init`

Initialize Krayne with kubeconfig and Kubernetes context. Saves settings to `~/.krayne/config.yaml`.

```
krayne init [OPTIONS]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `-k`, `--kubeconfig` | — | Path to kubeconfig file (skips interactive prompt) |
| `-c`, `--context` | — | Kubernetes context name (skips interactive prompt) |

**Examples:**

```bash
# Interactive mode — select kubeconfig and context from menus
krayne init

# Non-interactive mode
krayne init --kubeconfig ~/.kube/config --context my-context
```

```title="Terminal output"
╭─ Krayne Initialized ─────────────────────────╮
│  Kubeconfig:   ~/.kube/config               │
│  Context:      my-context                   │
╰─────────────────────────────────────────────╯
```

!!! note
    In interactive mode, Krayne presents a menu to select the kubeconfig source (default location, sandbox, or custom path), then lists available contexts.

---

## `krayne create`

Create a new Ray cluster.

```
krayne create <name> [OPTIONS]
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
| `--cpus-in-head` | `1` | CPU count for the head node (clamped up to `1` minimum in the manifest) |
| `--memory-in-head` | `4Gi` | Memory for the head node (clamped up to `4Gi` minimum in the manifest) |
| `--workers` | `0` | Desired worker replicas (initial count) |
| `--min-workers` | `0` | Minimum worker replicas for autoscaling |
| `--max-workers` | `1` | Maximum worker replicas for autoscaling |
| `--no-autoscaling` | `false` | Disable autoscaling (pin replicas) |
| `--timeout` | `300` | Timeout in seconds |
| `--file`, `-f` | — | Path to a YAML config file |

**Examples:**

```bash
# Minimal — all defaults (autoscaling 0-1 workers)
krayne create my-cluster

# GPU cluster with 2 workers, autoscaling 0-4
krayne create gpu-cluster --gpus-per-worker 1 --workers 2 --max-workers 4

# Fixed replicas (no autoscaling)
krayne create my-cluster --no-autoscaling --workers 4

# From YAML config
krayne create my-cluster --file cluster.yaml

# JSON output (note: -o/--output is a global option — it must come before the subcommand)
krayne -o json create my-cluster
```

![krayne create output](../assets/cli-create.png)

!!! tip "Local access"
    Use `krayne tun-open <cluster-name>` to create localhost mirrors of all cluster services via `kubectl port-forward`. Use `krayne tun-close <cluster-name>` to stop.

!!! note
    When using `--file`, the `name` argument and any CLI flags override the corresponding values in the YAML file.

---

## `krayne get`

List all Ray clusters in a namespace.

```
krayne get [OPTIONS]
```

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |

**Examples:**

```bash
# List clusters in default namespace
krayne get

# List clusters in a specific namespace
krayne get -n ml-team

# JSON output for scripting (global option goes before the subcommand)
krayne -o json get
```

![krayne get output](../assets/cli-get.png)

---

## `krayne describe`

Show detailed information about a cluster, including head node and worker group resource allocations.

```
krayne describe <name> [OPTIONS]
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
krayne describe my-cluster
krayne -o json describe my-cluster -n ml-team
```

![krayne describe output](../assets/cli-describe.png)

---

## `krayne scale`

Scale a worker group of a cluster to a target replica count.

```
krayne scale <name> [OPTIONS]
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
| `-r`, `--replicas` | — | Target desired replica count |
| `--min-replicas` | — | Minimum replicas for autoscaling |
| `--max-replicas` | — | Maximum replicas for autoscaling |

At least one of `--replicas`, `--min-replicas`, or `--max-replicas` is required.

**Examples:**

```bash
# Scale default worker group to 4 desired replicas
krayne scale my-cluster --replicas 4

# Adjust autoscaling bounds
krayne scale my-cluster --min-replicas 1 --max-replicas 10

# Scale a named worker group
krayne scale my-cluster --worker-group gpu-workers --replicas 8 -n ml-team
```

![krayne scale output](../assets/cli-scale.png)

---

## `krayne delete`

Delete a Ray cluster.

```
krayne delete <name> [OPTIONS]
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
krayne delete my-cluster

# Skip confirmation
krayne delete my-cluster --force

# Delete from specific namespace
krayne delete my-cluster -n ml-team --force
```

![krayne delete output](../assets/cli-delete.png)

---

## `krayne tun-open`

Start tunnels for cluster services to localhost via `kubectl port-forward`. Processes run in the background — use `tun-close` to stop them.

Both commands are **idempotent**: starting an already-active tunnel is a no-op (shows the existing tunnel info), and closing a non-existent tunnel is a no-op.

```
krayne tun-open <name> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `name` | Cluster name (required) |

**Options:**

| Option | Default | Description |
|---|---|---|
| `-n`, `--namespace` | `default` | Kubernetes namespace |

Local ports are deterministically assigned from the cluster name and namespace, so the same cluster always gets the same local ports.

**Examples:**

```bash
# Start tunnels for all services on a cluster
krayne tun-open my-cluster

# Start tunnels in a specific namespace
krayne tun-open my-cluster -n ml-team

# Get tunnel info as JSON (global option goes before the subcommand)
krayne -o json tun-open my-cluster
```

![krayne tun-open output](../assets/cli-tun-open.png)

!!! note
    The cluster must be in `ready` or `running` state. Tunnels forward to the head Service (`svc/<name>-head-svc`), which survives pod restarts.

---

## `krayne tun-close`

Stop tunnels for a cluster. Terminates all background `kubectl port-forward` processes.

```
krayne tun-close <name> [OPTIONS]
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
krayne tun-close my-cluster
krayne tun-close my-cluster -n ml-team
```

---

## `krayne sandbox setup`

Set up a local k3s cluster with KubeRay for development.

```
krayne sandbox setup
```

Requires Docker with at least 2 CPUs and 4 GB RAM. Creates a k3s container named `krayne-sandbox` (limited to 2 CPUs and 6 GB) and installs the KubeRay operator.

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

After setup, run `krayne init` to select the sandbox kubeconfig and context.

---

## `krayne sandbox teardown`

Tear down the local sandbox cluster.

```
krayne sandbox teardown
```

Removes the Docker container, deletes the sandbox kubeconfig, and clears Krayne settings if they point to the sandbox.

---

## `krayne sandbox status`

Show current status of the sandbox.

```
krayne sandbox status
```

![krayne sandbox status output](../assets/cli-sandbox-status.png)

---

## `krayne tui`

Launch the interactive terminal UI (see the [Interactive TUI guide](../guide/interactive-tui.md) for keybindings and screens).

```
krayne tui
```

---

## Output formats

### Table (default)

Rich-formatted tables and panels for human-readable output:

```bash
krayne get
krayne describe my-cluster
```

### JSON

Machine-readable JSON output, useful for scripting (`-o`/`--output` is a global option, so it must come **before** the subcommand):

```bash
krayne -o json get
krayne -o json describe my-cluster | jq '.info.status'
```

---

## Error handling

Errors are displayed as Rich panels by default. Use `--debug` (a global option, so it must come **before** the subcommand) to see full Python tracebacks:

```bash
krayne --debug describe nonexistent-cluster
```

![krayne error output](../assets/cli-error.png)

All errors are instances of `KrayneError` subclasses. See [Error Types](errors.md) for the full exception hierarchy.
