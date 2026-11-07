# twisk

**A k9s-style terminal UI for [SkyPilot](https://github.com/skypilot-org/skypilot).** *(twisk = TUI + sky)*
Browse and drive clusters, managed jobs, services and volumes from one keyboard-driven screen, and create them
through forms instead of hand-writing YAML.

```
 Server:   ● http://127.0.0.1:46580          <c> Create      <enter> Jobs   <l> Logs        _            _     _
 Source:   ~/.twisk/config.yaml              <s> Shell       <x> Exec       <p> Stop       | |___      _(_)___| | __
 User:     alice                             <u> Start       <a> Autostop   <ctrl-d> Down  | __\ \ /\ / / / __| |/ /
 Scope:    mine  ws: default  (U)            <d> Describe    <y> YAML       <e> Edit       | |_ \ V  V /| \__ \   <
 SkyPilot: 0.14.2 (api 62) · twisk 0.1.0     <1> clusters <2> jobs <3> services <4> pools   \__| \_/\_/ |_|___/_|\_\
                                  Clusters(all)[3]  9 GPUs up · 8 idle ≥1h
 NAME       STATUS  INFRA               RESOURCES                     GPUS  GPU-HRS  IDLE          AUTOSTOP
 dev-gpu    UP      Kubernetes/default  1x Kubernetes(4CPU--16GB--L4) 1     3.0      busy (1 job)  30m
 idle-h100  UP      Kubernetes/default  1x Kubernetes(H100:8)         8     48.0     5h00m         -
 <clusters>
```

> twisk lives in this repository next to krayne but is a separate project with its own `pyproject.toml`. It does
> not import or depend on krayne.

## Why

SkyPilot is configured with task YAML. That's powerful, but for day-to-day work you end up keeping a folder of
half-remembered YAML files and a terminal full of `sky status`, `sky logs`, `sky jobs queue`… twisk keeps the power
and removes the bookkeeping:

- **Forms, not YAML.** Press `c` anywhere to create a cluster, managed job, service or volume. Each form validates as
  you type and shows the exact SkyPilot YAML it generates in a live preview pane.
- **A spec library you never have to manage, but can.** Everything you create is saved as plain SkyPilot YAML under
  `~/.twisk/specs/<kind>/<name>.yaml`. Press `e` to re-open the form, `E` to open the YAML in `$EDITOR` (or the
  built-in editor), `R` to relaunch from it. Fields the form doesn't know about (e.g. `file_mounts`,
  `instance_type`, `config:`) are preserved across form edits. The files work with the plain CLI too:
  `sky launch -c dev ~/.twisk/specs/clusters/dev.yaml`.
- **Everything in one place, live.** Clusters, cluster job queues, managed jobs, services and their replicas, job
  pools and their workers, volumes, storage buckets, GPU usage, in-flight API requests, enabled infra and
  workspaces — auto-refreshing, filterable, sortable.
- **GPU-hours and idle time, not dollars.** Every cluster shows its GPU count, GPU-hours so far and how long it has
  been idle (busy while jobs run; otherwise idle since its last job ended, the same notion SkyPilot's autostop uses).
  GPU clusters idle for an hour or more are flagged red, and the title bar counts GPUs up vs. GPUs sitting idle.
  `:usage` lists GPU-hours per cluster — including torn-down ones — over 1d/7d/30d/all. No prices are shown:
  Kubernetes has none unless an admin configures them. (Interactive SSH sessions aren't visible to twisk, so a
  cluster someone is using only over SSH reads as idle.)
- **Mine vs. everyone, and workspaces.** `U` toggles between your resources and every user's (default: yours, so
  `ctrl-d` isn't one key from a teammate's cluster). `:ws` lists workspaces; `enter` switches the one new launches go
  to (via a SkyPilot project-config overlay — your `~/.sky/config.yaml` is never edited).
- **Knows SkyPilot's sharp edges.** e.g. Kubernetes clusters can only auto-*down*, never stop; a service's port has
  to be both `service.ports` and an opened resource port. The forms encode this so launches don't fail late.
- **Logs and shells one key away.** `l` streams logs (cluster jobs, managed jobs, controllers, load balancers,
  replicas, API requests); `s` drops you into an SSH shell on a cluster; `x` runs a command with `sky exec`.
- **Safe by default.** Destructive actions are on `ctrl-d` and always confirm. twisk never auto-starts a local API
  server behind your back; it tells you when the server is unreachable instead.

## Install & run

```bash
cd twisk
uv sync
uv run twisk                 # TUI against your SkyPilot API server
uv run twisk --demo          # TUI against an in-memory simulation (no server needed)
```

twisk talks to a [SkyPilot API server](https://docs.skypilot.co/en/latest/reference/api-server/api-server.html).
The endpoint is resolved in this order: `--endpoint URL`, `$SKYPILOT_API_SERVER_ENDPOINT`, `endpoint:` in
`~/.twisk/config.yaml`, `api_server.endpoint` in `~/.sky/config.yaml` (what `sky api login` writes), then
`http://127.0.0.1:46580`.

## Try it: the k3s sandbox

`twisk sandbox` gives you a disposable SkyPilot setup on your laptop: a single-node k3s cluster and a SkyPilot API
server wired to it, both in Docker. Nothing touches your real clouds.

```bash
uv run twisk sandbox up       # ~3–5 min the first time (pulls k3s, SkyPilot and the SkyPilot pod image)
uv run twisk                  # the sandbox is now twisk's default endpoint
uv run twisk sandbox status
uv run twisk sandbox start    # after a reboot / Docker restart
uv run twisk sandbox down     # removes everything
```

Requirements: Docker with ~4 CPUs / 8 GB available. In the sandbox use `infra: k8s` (or leave infra empty), keep
requests small (1 CPU / 1–2 GB), and note that Kubernetes clusters can be torn down but not stopped.

| | |
|---|---|
| API server | `http://127.0.0.1:46580` (also usable with the plain `sky` CLI via `SKYPILOT_API_SERVER_ENDPOINT`) |
| kubeconfig | `~/.twisk/sandbox/kubeconfig` (`kubectl --kubeconfig ~/.twisk/sandbox/kubeconfig get pods`) |
| server logs | `twisk sandbox logs` |
| service endpoints | SkyServe load balancers listen on `http://localhost:30001`–`30020` (published from the sandbox) |
| pod images | `twisk sandbox preload [IMAGE…]` side-loads images from your host's Docker into k3s |
| workspaces | `default` and `experiments`, to try workspace switching (`:ws`) |

What `sandbox up` does: starts `rancher/k3s` (API on `127.0.0.1:16443`), starts `berkeleyskypilot/skypilot:0.14.2`
with `sky api start --deploy` in the k3s container's network namespace, gives the server the k3s kubeconfig plus a
small config (managed-jobs/serve controllers run inside the API server; pods use `imagePullPolicy: IfNotPresent`),
pre-loads SkyPilot's pod image into k3s from your host's Docker cache (so recreating the sandbox doesn't re-download
it), runs `sky check kubernetes`, and points twisk at it. It detects cgroup-v1 hosts and nested-container hosts that
forbid lowering `oom_score_adj` and adjusts k3s accordingly.

The sandbox is one k3s node with whatever CPUs Docker gives it. If a launch sits in INIT or fails with
"Failed to acquire resources", the node is full: tear down clusters/services you no longer need (`ctrl-d`).

Behind a TLS-intercepting corporate proxy, pass a CA bundle that includes your proxy's CA:
`twisk sandbox up --ca-bundle /path/to/ca-bundle.crt`. It is trusted by k3s (image pulls), the API server (catalog
downloads) and every SkyPilot pod (pip/uv/curl during runtime setup).

## Keys

| Global | |
|---|---|
| `:` | command mode: `:clusters` `:jobs` `:services` `:pools` `:volumes` `:storage` `:usage` `:specs` `:requests` `:infra` `:ws` `:q` (aliases: `cl`, `mj`, `svc`, `pl`, `vol`, `st`, `gpu`, `sp`, `req`, …) |
| `1`–`9`, `0` | jump to a view: clusters, jobs, services, pools, volumes, storage, usage, specs, requests, infra |
| `U` | scope: only my resources ⇄ every user's |
| `/` | filter rows, `esc` clears |
| `<` `>` | sort by previous/next column (again to reverse) |
| `ctrl-r` | refresh now (on clusters: ask the clouds for fresh status) |
| `esc` / `q` | back (q quits at the top level) · `?` help · `ctrl-c` quit |

| Clusters | | Managed jobs | |
|---|---|---|---|
| `c` | create (form) | `c` | submit (form) |
| `enter` | job queue on the cluster | `l` / `enter` | job logs |
| `l` | logs of the latest job | `L` | controller logs |
| `s` | SSH shell | `ctrl-d` | cancel |
| `x` | `sky exec` a command | `R` | resubmit from spec |
| `p` / `u` | stop / start | | |
| `a` | autostop / autodown | **Services** | |
| `ctrl-d` | tear down | `enter` | replicas (`l` logs, `ctrl-d` terminate) |
| `d` / `y` | describe / YAML | `l` / `L` | load balancer / controller logs |
| `e` / `E` | edit spec in form / as YAML | `e` / `R` | edit, rolling update |
| `R` | relaunch from spec | `ctrl-d` | tear down |
| `i` | import the server's YAML into the library | | |

| Spec library (`5`) | | Requests (`6`) | |
|---|---|---|---|
| `enter` | launch / apply | `enter` | follow logs |
| `e` / `E` | edit in form / as YAML | `ctrl-d` | cancel |
| `D` | duplicate | | |
| `ctrl-d` | delete the file | **Forms** | |
| `o` | show (and copy) the path | `ctrl-s` | save spec + launch/apply |
| | | `ctrl-o` / `ctrl-e` | save only / continue as YAML |

| Pools (`4`) | | GPU usage (`7`) | |
|---|---|---|---|
| `c` | create (form) | `t` | cycle window: 1d / 7d / 30d / all |
| `enter` | workers (`l` worker logs) | `d` | describe |
| `l` | controller logs | **Storage (`6`)** | |
| `ctrl-d` | tear down | `ctrl-d` | delete bucket (and its objects) |
| `e` / `R` | edit / re-apply (rolling) | **Workspaces (`:ws`)** | |
| | | `enter` | switch the active workspace |

Managed jobs have a **Run in pool** field: set it to submit the job to a pool's warm workers instead of
provisioning a cluster (`sky jobs launch --pool`). Since the pool isn't part of SkyPilot's task YAML, the spec file
records it in a `# twisk: pool=<name>` header comment.

Log views: `f` toggle follow, `w` toggle wrap, `s` save to `~/.twisk/logs/`, `esc` detach (the work keeps running).

## Layout

```
twisk/
  src/twisk/
    specs/        typed specs ⇄ SkyPilot YAML, and the on-disk spec library
    backend/      Backend protocol; skypilot.py (SDK, API-server mode) and fake.py (demo/tests)
    tui/          Textual app: views.py (resource tables), forms.py (form schemas), screens/, widgets/
    sandbox.py    k3s + SkyPilot API server sandbox
    cli.py        `twisk`, `twisk sandbox …`, `twisk specs …`
  tests/          unit + Textual pilot tests (fake backend); `-m sandbox` for live checks
```

## Development

```bash
uv sync
uv run pytest                    # unit + TUI tests (fake backend, ~45 s)
uv run pytest -m sandbox         # live checks against a running `twisk sandbox`
```
