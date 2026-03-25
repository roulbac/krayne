# Prism

**CLI and SDK for creating, managing, and scaling Ray clusters on Kubernetes.**

Prism wraps the [KubeRay](https://ray-project.github.io/kuberay/) operator behind a clean, opinionated interface so ML practitioners can get distributed compute without touching Kubernetes manifests.

> *A prism takes a single beam of light and organizes it into something structured and useful. Prism does the same for Ray clusters.*

## Quickstart

```bash
pip install prism-sdk
```

Create a Ray cluster with a single command:

```bash
prism create my-cluster --gpus-per-worker 1 --workers 2
```

Or use the Python SDK:

```python
from prism.api import create_cluster
from prism.config import ClusterConfig, WorkerGroupConfig

config = ClusterConfig(
    name="my-cluster",
    worker_groups=[WorkerGroupConfig(replicas=2, gpus=1, gpu_type="a100")],
)
info = create_cluster(config, wait=True)
print(info.dashboard_url)
```

## Features

- **Zero-config defaults** — every command works with no flags. Sensible defaults get you a working cluster instantly.
- **CLI and SDK** — the CLI is a thin shell over the Python SDK. Anything you do from the terminal, you can do from code.
- **Functional API** — stateless free functions, not class hierarchies. Easy to test, easy to compose.
- **Pydantic config** — validated configuration with YAML override support. No silent failures.
- **Rich output** — beautiful terminal tables via Rich, with `--output json` for scripting.

## CLI Overview

```
prism create <name>      Create a new Ray cluster
prism get                List clusters in a namespace
prism describe <name>    Show detailed cluster info
prism scale <name>       Scale a worker group
prism delete <name>      Delete a cluster
```

All commands support `-n/--namespace`, `--output json`, and `--debug` flags.

## Documentation

Full documentation is available at the [Prism docs site](https://roulbac.github.io/prism/).

- [Getting Started](https://roulbac.github.io/prism/getting-started/)
- [CLI Reference](https://roulbac.github.io/prism/cli/)
- [Python SDK Reference](https://roulbac.github.io/prism/sdk/)
- [Configuration](https://roulbac.github.io/prism/configuration/)
- [Architecture](https://roulbac.github.io/prism/architecture/)
- [Error Handling](https://roulbac.github.io/prism/errors/)

## Requirements

- Python 3.10+
- A Kubernetes cluster with the [KubeRay operator](https://ray-project.github.io/kuberay/) installed
- A valid kubeconfig (or running inside the cluster)

## Development

```bash
# Clone and install
git clone https://github.com/roulbac/prism.git
cd prism
pip install -e ".[dev]"

# Run tests
pytest

# Run integration tests (requires a K8s cluster)
pytest -m integration
```

## License

Apache 2.0
