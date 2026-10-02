#!/usr/bin/env bash
# .github/scripts/ci-changes.sh [<base-sha> <head-sha>] — decide which CI jobs a change needs.
#
# Prints key=value lines (appended to $GITHUB_OUTPUT when set):
#   terraform_modules  JSON array of module dirs to validate ([] = none)
#   k8s | ansible | docs | workflows | aiops   true/false
#
# No base (push to main, workflow_dispatch) or a change to CI itself/shared
# lint config => run everything. This exists so the single required check
# ("CI gate") can stay always-running: skipped jobs count as passing there,
# whereas a path-filtered *required* workflow would hang forever on PRs that
# don't match its filter.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

base="${1:-}"; head="${2:-HEAD}"
all_modules() { find terraform -name '*.tf' -not -path '*/.terraform/*' -exec dirname {} \; | sort -u; }

if [ -z "$base" ]; then
  changed="ALL"
else
  changed="$(git diff --name-only "$base...$head")"
  if grep -Eq '^(\.github/|\.yamllint\.yml$|\.gitleaks\.toml$|ansible/\.ansible-lint$|ansible/requirements\.yml$)' <<<"$changed"; then
    changed="ALL"
  fi
fi

has() { [ "$changed" = "ALL" ] || grep -Eq "$1" <<<"$changed"; }

if [ "$changed" = "ALL" ]; then
  mods="$(all_modules)"
else
  mods=""
  while IFS= read -r f; do
    [[ "$f" == terraform/* ]] || continue
    d="$(dirname "$f")"
    # nearest ancestor that is a module (holds *.tf); skips READMEs etc. outside one
    while [ "$d" != "." ] && ! compgen -G "$d/*.tf" >/dev/null; do d="$(dirname "$d")"; done
    [ "$d" != "." ] && [ "$d" != "terraform" ] && mods+="$d"$'\n'
  done <<<"$changed"
  mods="$(printf '%s' "$mods" | sort -u | sed '/^$/d')"
fi

emit() { echo "$1=$2"; [ -z "${GITHUB_OUTPUT:-}" ] || echo "$1=$2" >>"$GITHUB_OUTPUT"; }
emit terraform_modules "$(printf '%s\n' "$mods" | sed '/^$/d' | python3 -c 'import json,sys; print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')"
emit k8s       "$(has '^k8s/' && echo true || echo false)"
emit ansible   "$(has '^ansible/' && echo true || echo false)"
emit docs      "$(has '\.md$' && echo true || echo false)"
emit workflows "$(has '^\.github/workflows/' && echo true || echo false)"
# aiops/ is cross-linked into the docs (runbook markers), the Semaphore templates and
# the aiops-* playbooks, so a change to any of those can break its consistency checks.
# The n8n workflow check (aiops/n8n/) also reads the n8n-agent role defaults and the
# ingest-token locals in terraform/vault.
emit aiops     "$(has '^(aiops/|ansible/playbooks/aiops-|ansible/roles/n8n-agent/|terraform/(semaphore|vault)/|docs/(known-issues|procedures|services)/)' && echo true || echo false)"
