# Nightly QA routine

This sets up a [Claude Code routine](https://code.claude.com/docs/en/routines) that runs
krayne's test suites every night on Anthropic-managed cloud infrastructure, then posts the
result as a GitHub **commit status** on the latest commit of `main` — so it shows up as a
green / red check, the same way CI does.

It runs on your Claude subscription (no `ANTHROPIC_API_KEY`), and draws down your normal
subscription usage plus the per-account daily routine cap.

## Why a routine instead of GitHub Actions

- Authenticates with your **Claude subscription**, not a metered API key.
- Runs on Anthropic's cloud, so it keeps working with your laptop closed.
- The QA logic is a prompt you can iterate on, not YAML.

The trade-off: a routine's run status only tells you the *session* finished without an infra
error — **not** whether QA passed. That's exactly why the routine ends by posting an explicit
commit status with `scripts/claude_qa_status.sh`; that status is the real signal.

## One-time setup

1. **Install the Claude GitHub App** on `roulbac/krayne` (only needed if you also want
   GitHub-event triggers later; the nightly schedule alone does not require it, but the app
   makes PR comments cleaner). <https://github.com/apps/claude>

2. **Create a GitHub token** with `repo:status` scope (fine-grained: *Commit statuses →
   Read and write* on `roulbac/krayne`). This is what lets the routine write the check.

3. **Create the routine** at <https://claude.ai/code/routines> → **New routine**:
   - **Repository**: `roulbac/krayne`
   - **Environment**: use one whose **setup script** installs deps, and add the token as an
     environment variable `GH_TOKEN`. Keep network access on **Trusted** — `api.github.com`,
     PyPI, and the astral (uv) domains are all in the default allowlist.
     - Suggested setup script: `curl -LsSf https://astral.sh/uv/install.sh | sh && uv sync`
   - **Trigger**: **Schedule → daily**, pick your nightly wall-clock time. (CLI alternative:
     `/schedule daily krayne QA at 2am`.)
   - **Prompt**: paste the prompt below.

> **Integration tests and Docker.** `krayne sandbox setup` spins up a Docker-in-Docker
> Kubernetes cluster (Docker + kubectl + Helm). The Claude Code cloud environment **ships
> Docker** (`docker`, `dockerd`, `docker compose` are pre-installed and Docker Hub is in the
> Trusted allowlist), so the full suite can run by default — no custom image needed. The two
> things to watch: the Docker daemon may need to be started in the session (`dockerd` or
> `sudo service docker start`), and the sandbox checks for a minimum CPU/memory, so a
> resource-constrained environment can still fail `krayne sandbox setup`. The prompt below
> keeps a `docker info` guard so that if Docker genuinely isn't reachable, it reports
> unit-test results only rather than failing the whole check on a missing prerequisite.

## The routine prompt

Paste this into the routine's **Instructions** box:

```text
You are running krayne's nightly QA on the default branch. Work autonomously and finish by
posting a single GitHub commit status. Do NOT open a pull request or push any code.

Steps:

1. Record the commit under test: SHA = `git rev-parse HEAD`. All status posts target this SHA.

2. Mark the check in progress:
   scripts/claude_qa_status.sh --state pending --description "nightly QA running"

3. Install deps if the setup script hasn't already:  uv sync

4. Run the unit suite (this is the hard gate):
     uv run pytest tests/unit -v --timeout=60
   Capture pass/fail and the count of failures.

5. Run integration tests (Docker + kubectl + Helm via the krayne sandbox). First make sure
   the Docker daemon is up: if `docker info` fails, try to start it (`sudo service docker
   start` or `sudo dockerd >/tmp/dockerd.log 2>&1 &` then wait for `docker info` to succeed).
   Once Docker responds:
     uv run pytest tests/integration -v -m integration --timeout=600
   Only if Docker still cannot be started after a reasonable attempt, skip integration tests
   and note "integration skipped: no Docker" in the description — do NOT treat an unreachable
   Docker daemon as a test failure.

6. Post the final commit status with scripts/claude_qa_status.sh:
   - All run suites passed  -> --state success
       description e.g. "unit ok; integration ok" or "unit ok; integration skipped (no Docker)"
   - Any suite that actually ran had failures -> --state failure
       description e.g. "unit failed: 3 of 120" — keep it under 140 chars
   - The run could not execute the suites at all (e.g. uv sync failed) -> --state error
       description e.g. "setup failed: uv sync error"

7. In your final message, summarize: the SHA, which suites ran, pass/fail counts, and the
   exact status you posted. If anything failed, include the most relevant ~30 lines of pytest
   output so a human can triage from the session transcript.

Use `--sha <SHA>` on every status call so all three posts (pending, then final) land on the
same commit even if the working tree moves.
```

## What you'll see

On <https://github.com/roulbac/krayne/commits/main> each nightly commit picks up a
`claude-nightly-qa` check that's yellow while running, then green or red. Click it to jump to
the routine session transcript (the status links there via `CLAUDE_SESSION_URL` if your
environment sets it).

## First run: verify before trusting it

Don't wait for the first scheduled night. On the routine's detail page click **Run now**, then
open that session and confirm the routine behaves end-to-end. A green entry in the run list only
means the session didn't hit an infra error — it does **not** mean QA passed — so check the
transcript itself:

1. **Status posts landed.** The `claude-nightly-qa` check should appear on the HEAD commit of
   `main` at <https://github.com/roulbac/krayne/commits/main> — yellow while running, then
   green/red. If it never appears, the token or its scope is wrong; the transcript will show the
   `claude_qa_status: failed (HTTP 4xx)` line and GitHub's error body. Common causes: token
   lacks `repo:status`, or `GH_TOKEN` wasn't set as an environment variable on the routine.
2. **Unit tests actually ran** (`uv run pytest tests/unit` output is in the transcript, not an
   `uv: command not found` — if so, fix the setup script).
3. **Integration tests: did the sandbox come up?** This is the one to watch on the first run.
   Look for whether `dockerd` started and `krayne sandbox setup` succeeded. The two realistic
   failure modes:
   - `Cannot connect to the Docker daemon` — the daemon wasn't started; confirm the prompt's
     `service docker start` / `dockerd &` step ran, or add daemon startup to the setup script.
   - A `DockerNotFoundError`-style message about insufficient CPU/memory — the environment is
     under the sandbox's resource minimum. If the cloud environment can't be sized up, decide
     whether unit-only nightly QA is acceptable and adjust the prompt's expectation, or move
     integration coverage back to GitHub Actions where the runner is sized for it.
4. **The final status matches reality.** Cross-check the posted state against the transcript:
   a passing run should be `success`, a real test failure `failure`, and a setup breakage
   `error` (not `failure`). If the mapping is off, tighten the wording in prompt steps 5–6.

Only after a clean **Run now** should you rely on the nightly schedule. Re-verify after any edit
to the prompt, the setup script, or the environment's network/resources.

## Optional: make it a required check / open issues on failure

- **Block merges on regressions**: add `claude-nightly-qa` as a required status check in the
  branch protection rules for `main`. (Note: nightly status lands on whatever was HEAD at
  run time, so prefer this for visibility rather than as a strict merge gate.)
- **File an issue on failure**: extend the prompt's step 6 to also run
  `gh issue create ...` (needs `gh` + a token with `issues:write`) or use a connector, so a
  red night opens a tracked issue instead of only a red check.

## Testing the helper locally

```bash
GH_TOKEN=<token-with-repo:status> GITHUB_REPOSITORY=roulbac/krayne \
  scripts/claude_qa_status.sh --state success --description "manual smoke test"
```
