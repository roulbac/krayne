# Krayne end-to-end QA — runtime instructions for Claude Code

These are the instructions Claude Code follows when invoked by the
`claude-e2e-qa` GitHub Actions workflow. The workflow runs on a GitHub-hosted
`ubuntu-latest` runner, which has **unrestricted outbound network** (no egress
allowlist) and Docker preinstalled and running. That is the key reason this QA
runs reliably on Actions: the image pulls (`quay.io`, Docker Hub) and the
in-pod service bootstraps (`pip install notebook`, the code-server download
from GitHub releases) all reach the network without proxy interference.

Work through the steps in order. Treat the unit suite as a hard gate. Report a
clear, structured summary at the end whether you pass or fail.

## 1. Record what's under test

Capture and report the commit SHA and short message:

```bash
git rev-parse HEAD
git log -1 --oneline
uv --version
```

## 2. Install dependencies

```bash
uv sync
```

## 3. Unit gate (hard gate — must pass)

```bash
uv run pytest tests/unit -v --timeout=60
```

Capture the pass/fail counts. If the unit suite fails, you may still run the
integration suite for additional signal, but the overall result is a FAILURE.

## 4. Confirm Docker is available

GitHub-hosted runners ship Docker running. Just confirm:

```bash
docker info >/dev/null 2>&1 && docker version --format 'server {{.Server.Version}}'
```

If `docker info` fails, report it — do not attempt to start `dockerd` manually
on the hosted runner.

## 5. Integration suite

```bash
uv run pytest tests/integration -v -m integration --timeout=600
```

The session-scoped fixture runs `setup_sandbox()`: a k3s-in-Docker container, a
runc wrapper for cgroup compatibility, a KubeRay Helm install from a
GitHub-releases chart URL, and the operator image pull from `quay.io`. The
service tests then create a Ray cluster and tunnel to its dashboard, notebook,
code-server, and SSH endpoints.

## 6. Diagnose failures before teardown

The integration fixture runs `docker rm -f krayne-sandbox` on teardown, which
wipes cluster state. If the suite is failing, gather evidence **while the run
is in progress / before teardown** where you can:

```bash
docker exec krayne-sandbox kubectl get pods -A
docker exec krayne-sandbox kubectl describe pod -n default -l app.kubernetes.io/name=kuberay-operator
```

For the notebook / code-server service probes (these bootstrap inside the head
pod at runtime), the relevant logs live in the head pod:

```bash
# head pod runs jupyter + code-server bootstrap; logs land in /tmp
docker exec krayne-sandbox kubectl logs -n default <head-pod> | tail -50
docker exec krayne-sandbox kubectl exec -n default <head-pod> -- sh -c 'cat /tmp/jupyter.log /tmp/code-server.log' 2>/dev/null
```

Known transient (not a real failure): a flannel CNI race where
`/run/flannel/subnet.env` is briefly missing — it self-resolves within a few
seconds.

## 7. Report

Produce a structured summary covering:

- **SHA under test** (and short commit message)
- **Unit:** N passed / M failed
- **Integration:** N passed / M failed — or, if blocked, the blocking error
  with ~30 lines of the most relevant output
- **Verdict:** PASS only if both suites are green; otherwise FAIL with the
  root cause called out

Then, as the final action — **always, whether QA passed or failed** — write
two files to the repository root (use Bash; the `Write` tool is not enabled):

1. **`qa-report.md`** — the full structured summary above, in Markdown. This is
   published to the GitHub Actions run summary.
2. **`qa-verdict.txt`** — a single line containing exactly `PASS` (both suites
   green) or `FAIL` (anything else), and nothing else. A later workflow step
   reads this to set the job's success/failure state, so it must always be
   written and must be one of those two exact tokens.

Example:

```bash
cat > qa-report.md <<'EOF'
# Krayne E2E QA — <short SHA>
...
EOF
printf 'PASS\n' > qa-verdict.txt   # or FAIL
```

Do not push code, do not open or modify pull requests, and do not change repo
state beyond running the suites and writing these two report files. This is a
read-only QA run.
