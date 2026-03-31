from __future__ import annotations

import traceback
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from prism import __version__
from prism.errors import PrismError

app = typer.Typer(
    name="prism",
    help="Create, manage, and scale Ray clusters on Kubernetes.",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True)

# Global options stored on the context
_debug: bool = False
_output_json: bool = False
_kubeconfig: str | None = None


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"prism {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", callback=_version_callback, is_eager=True,
        help="Show version and exit.",
    ),
    debug: bool = typer.Option(False, "--debug", help="Show full tracebacks on error."),
    output: str = typer.Option("table", "--output", "-o", help="Output format: table or json."),
    kubeconfig: Optional[str] = typer.Option(None, "--kubeconfig", help="Path to kubeconfig file."),
) -> None:
    """Prism — Ray clusters made simple."""
    global _debug, _output_json, _kubeconfig
    _debug = debug
    _output_json = output == "json"
    _kubeconfig = kubeconfig


def _handle_error(exc: Exception) -> None:
    if _debug:
        err_console.print_exception()
    else:
        err_console.print(Panel(str(exc), title="Error", border_style="red"))
    raise typer.Exit(1)


from prism.api import (  # noqa: E402
    create_cluster as _create_cluster,
    delete_cluster as _delete_cluster,
    describe_cluster as _describe_cluster,
    get_cluster as _get_cluster,
    list_clusters as _list_clusters,
    scale_cluster as _scale_cluster,
)
from prism.config import (  # noqa: E402
    ClusterConfig,
    PrismSettings,
    WorkerGroupConfig,
    load_config_from_yaml,
    DEFAULT_CPUS,
    DEFAULT_MEMORY,
    save_prism_settings,
)
from prism.output import (  # noqa: E402
    format_cluster_created,
    format_cluster_details,
    build_sandbox_progress_table,
    format_cluster_list,
    format_init_success,
    format_json,
    format_sandbox_setup_success,
    format_sandbox_status,
)
from prism.sandbox import (  # noqa: E402
    setup_sandbox as _setup_sandbox,
    teardown_sandbox as _teardown_sandbox,
    sandbox_status as _sandbox_status,
)


@app.command("create")
def create(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    gpus_per_worker: int = typer.Option(0, "--gpus-per-worker"),
    worker_gpu_type: str = typer.Option("t4", "--worker-gpu-type"),
    cpus_in_head: str = typer.Option(DEFAULT_CPUS, "--cpus-in-head"),
    memory_in_head: str = typer.Option(DEFAULT_MEMORY, "--memory-in-head"),
    workers: int = typer.Option(1, "--workers"),
    timeout: int = typer.Option(300, "--timeout"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="YAML config file."),
) -> None:
    """Create a new Ray cluster."""
    import time

    from rich.live import Live

    try:
        if file:
            overrides = {"name": name, "namespace": namespace}
            config = load_config_from_yaml(file, overrides=overrides)
        else:
            config = ClusterConfig(
                name=name,
                namespace=namespace,
                head={"cpus": cpus_in_head, "memory": memory_in_head},  # type: ignore[arg-type]
                worker_groups=[
                    WorkerGroupConfig(
                        replicas=workers,
                        gpus=gpus_per_worker,
                        gpu_type=worker_gpu_type,
                    )
                ],
            )

        info = _create_cluster(config, kubeconfig=_kubeconfig)

        if _output_json:
            format_json(info, console)
            return

        try:
            with Live(
                format_cluster_created(info, console, live=True),
                console=console,
                refresh_per_second=2,
            ) as live:
                while info.status not in ("ready", "running"):
                    time.sleep(2)
                    info = _get_cluster(
                        name, namespace, kubeconfig=_kubeconfig
                    )
                    live.update(
                        format_cluster_created(info, console, live=True)
                    )
        except KeyboardInterrupt:
            pass

        format_cluster_created(info, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("get")
def get(
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """List Ray clusters in a namespace."""
    try:
        clusters = _list_clusters(namespace, kubeconfig=_kubeconfig)
        if _output_json:
            format_json(clusters, console)
        else:
            format_cluster_list(clusters, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("describe")
def describe(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
) -> None:
    """Show detailed information about a cluster."""
    try:
        details = _describe_cluster(name, namespace, kubeconfig=_kubeconfig)
        if _output_json:
            format_json(details, console)
        else:
            format_cluster_details(details, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("scale")
def scale(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    worker_group: str = typer.Option("worker", "--worker-group", "-g"),
    replicas: int = typer.Option(..., "--replicas", "-r", help="Target replica count."),
) -> None:
    """Scale a worker group of a cluster."""
    try:
        info = _scale_cluster(name, namespace, worker_group, replicas, kubeconfig=_kubeconfig)
        if _output_json:
            format_json(info, console)
        else:
            format_cluster_created(info, console)
    except PrismError as exc:
        _handle_error(exc)


@app.command("delete")
def delete(
    name: str = typer.Argument(..., help="Cluster name."),
    namespace: str = typer.Option("default", "-n", "--namespace"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation."),
) -> None:
    """Delete a Ray cluster."""
    try:
        if not force:
            typer.confirm(
                f"Delete cluster '{name}' in namespace '{namespace}'?", abort=True
            )
        _delete_cluster(name, namespace, kubeconfig=_kubeconfig)
        console.print(f"Cluster '{name}' deleted.", style="green")
    except PrismError as exc:
        _handle_error(exc)


@app.command("init")
def init(
    kubeconfig: str = typer.Option(
        None, "--kubeconfig", "-k", help="Path to kubeconfig file (skips interactive prompt)."
    ),
    context: str = typer.Option(
        None, "--context", "-c", help="Kubernetes context name (skips interactive prompt)."
    ),
) -> None:
    """Initialise Prism with a kubeconfig and kube context.

    When run without flags an interactive menu is shown.  Pass --kubeconfig
    and --context for headless / CI usage.
    """
    import yaml as _yaml

    from prism.config.settings import DEFAULT_KUBECONFIG
    from prism.errors import ConfigValidationError
    from prism.sandbox.manager import SANDBOX_KUBECONFIG

    try:
        # --- resolve kubeconfig path -----------------------------------------
        if kubeconfig is None:
            import questionary

            CUSTOM_LABEL = "Custom path"
            kubeconfig_choices = [
                questionary.Choice(
                    f"Default kubeconfig ({DEFAULT_KUBECONFIG})",
                    value=str(DEFAULT_KUBECONFIG),
                ),
                questionary.Choice(
                    f"Sandbox kubeconfig ({SANDBOX_KUBECONFIG})",
                    value=str(SANDBOX_KUBECONFIG),
                ),
                questionary.Choice(CUSTOM_LABEL, value=CUSTOM_LABEL),
            ]
            selected = questionary.select(
                "Select kubeconfig source:",
                choices=kubeconfig_choices,
            ).ask()
            if selected is None:
                raise typer.Abort()
            if selected == CUSTOM_LABEL:
                kubeconfig = questionary.path(
                    "Enter kubeconfig path:",
                ).ask()
                if kubeconfig is None:
                    raise typer.Abort()
            else:
                kubeconfig = selected

        resolved = Path(kubeconfig).resolve()
        if not resolved.exists():
            raise ConfigValidationError(f"Kubeconfig file not found: {resolved}")

        # --- parse contexts from the kubeconfig ------------------------------
        raw = _yaml.safe_load(resolved.read_text()) or {}
        contexts = [c["name"] for c in raw.get("contexts", []) if "name" in c]
        if not contexts:
            raise ConfigValidationError(
                f"No contexts found in kubeconfig: {resolved}"
            )

        # --- resolve kube context --------------------------------------------
        if context is None:
            if len(contexts) == 1:
                context = contexts[0]
                console.print(f"Auto-selected context: [bold]{context}[/bold]")
            else:
                import questionary

                current = raw.get("current-context", "")
                ctx_choices = [
                    questionary.Choice(
                        f"{name}  (current)" if name == current else name,
                        value=name,
                    )
                    for name in contexts
                ]
                default = current if current in contexts else None
                context = questionary.select(
                    "Select kube context:",
                    choices=ctx_choices,
                    default=default,
                ).ask()
                if context is None:
                    raise typer.Abort()
        else:
            if context not in contexts:
                raise ConfigValidationError(
                    f"Context '{context}' not found in kubeconfig. "
                    f"Available: {', '.join(contexts)}"
                )

        save_prism_settings(
            PrismSettings(kubeconfig=str(resolved), kube_context=context)
        )
        format_init_success(str(resolved), context, console)
    except PrismError as exc:
        _handle_error(exc)


sandbox_app = typer.Typer(
    name="sandbox",
    help="Manage a local development sandbox (k3s + KubeRay).",
    no_args_is_help=True,
)
app.add_typer(sandbox_app)


@sandbox_app.command("setup")
def sandbox_setup() -> None:
    """Set up a local k3s cluster with KubeRay."""
    from rich.live import Live
    from prism.sandbox.manager import SETUP_STEPS

    steps: dict[str, str] = {s: "pending" for s in SETUP_STEPS}

    try:
        with Live(
            build_sandbox_progress_table(steps), console=console, refresh_per_second=4
        ) as live:

            def _on_progress(step: str, status: str) -> None:
                steps[step] = status
                live.update(build_sandbox_progress_table(steps))

            kubeconfig_path = _setup_sandbox(on_progress=_on_progress)

        format_sandbox_setup_success(kubeconfig_path, console)
    except PrismError as exc:
        _handle_error(exc)


@sandbox_app.command("teardown")
def sandbox_teardown() -> None:
    """Tear down the local sandbox cluster."""
    try:
        _teardown_sandbox()
        console.print("Sandbox removed.", style="green")
    except PrismError as exc:
        _handle_error(exc)


@sandbox_app.command("status")
def sandbox_status_cmd() -> None:
    """Show the current status of the sandbox."""
    try:
        status = _sandbox_status()
        if _output_json:
            format_json(status, console)
        else:
            format_sandbox_status(status, console)
    except PrismError as exc:
        _handle_error(exc)
