#!/usr/bin/env bash
#
# Post a GitHub commit status — used by the nightly Claude Code QA routine.
#
# A "status check" on GitHub is a commit status attached to a SHA (the
# Statuses API). This script sets one so a nightly QA run shows up as a
# green / red check on the latest commit of the default branch, the same
# way CI checks appear.
#
# Usage:
#   scripts/claude_qa_status.sh --state pending  --description "QA running"
#   scripts/claude_qa_status.sh --state success  --description "unit+integration passed"
#   scripts/claude_qa_status.sh --state failure  --description "unit tests failed"
#
# Options:
#   --state        one of: pending | success | failure | error   (required)
#   --description  short human-readable summary (<= 140 chars)    (default: "")
#   --context      label shown next to the check                  (default: "claude-nightly-qa")
#   --sha          commit SHA to attach to                        (default: current HEAD)
#   --url          target URL the check links to                  (default: the routine session URL if set)
#
# Environment:
#   GH_TOKEN / GITHUB_TOKEN   GitHub token with `repo:status` (or repo) scope   (required)
#   GITHUB_REPOSITORY         owner/repo                                         (default: roulbac/krayne)
#   CLAUDE_SESSION_URL        if set, used as the default --url target
#
# Requires: curl, python3 (used only for safe JSON encoding; always present here).

set -euo pipefail

state=""
description=""
context="claude-nightly-qa"
sha=""
target_url="${CLAUDE_SESSION_URL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --state)       state="$2";       shift 2 ;;
    --description) description="$2";  shift 2 ;;
    --context)     context="$2";     shift 2 ;;
    --sha)         sha="$2";         shift 2 ;;
    --url)         target_url="$2";  shift 2 ;;
    *) echo "claude_qa_status: unknown argument: $1" >&2; exit 2 ;;
  esac
done

token="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
repo="${GITHUB_REPOSITORY:-roulbac/krayne}"

if [[ -z "$token" ]]; then
  echo "claude_qa_status: set GH_TOKEN (or GITHUB_TOKEN) to a token with repo:status scope" >&2
  exit 1
fi

case "$state" in
  pending|success|failure|error) ;;
  *) echo "claude_qa_status: --state must be one of pending|success|failure|error (got '${state}')" >&2; exit 2 ;;
esac

if [[ -z "$sha" ]]; then
  sha="$(git rev-parse HEAD)"
fi

# GitHub caps description at 140 chars; trim defensively.
description="${description:0:140}"

# Build the JSON body with python3 so quotes / newlines in the description
# can never break the payload.
body="$(python3 - "$state" "$context" "$description" "$target_url" <<'PY'
import json, sys
state, context, description, target_url = sys.argv[1:5]
payload = {"state": state, "context": context, "description": description}
if target_url:
    payload["target_url"] = target_url
print(json.dumps(payload))
PY
)"

url="https://api.github.com/repos/${repo}/statuses/${sha}"

http_code="$(curl -sS -o /tmp/claude_qa_status_resp.json -w '%{http_code}' \
  -X POST "$url" \
  -H "Authorization: Bearer ${token}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "$body")"

if [[ "$http_code" == "201" ]]; then
  echo "claude_qa_status: set ${context}=${state} on ${repo}@${sha:0:12}"
else
  echo "claude_qa_status: failed (HTTP ${http_code})" >&2
  cat /tmp/claude_qa_status_resp.json >&2 || true
  exit 1
fi
