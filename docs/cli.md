# CLI Reference

Prism provides a command-line interface built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/) for managing Ray clusters on Kubernetes.

## Global options

These options are available on every command:

| Option | Description |
|---|---|
| `--version`, `-V` | Show version and exit |
| `--debug` | Show full tracebacks on error |
| `--output`, `-o` | Output format: `table` (default) or `json` |

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

All errors are instances of `PrismError` subclasses. See [Error Handling](errors.md) for the full exception hierarchy.
