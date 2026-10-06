#!/usr/bin/env bash
# .claude/hooks/session-start.sh — SessionStart hook for Claude Code CLOUD sessions.
#
# The repo is public and `.githooks/pre-push` secret-scans the entire repo before every push, but that
# hook is opt-in per clone and fails closed without a gitleaks binary. A fresh cloud container has neither,
# so this makes both true: install the gitleaks version CI pins and point git at .githooks.
#
# The pin (URL + sha256) is read from .github/workflows/ci.yml, the single source of truth, and installed
# through .github/scripts/install-tool.sh (sha256-verified, no floating "latest"). Idempotent: the container
# state is cached after the first run and resume/clear/compact re-run it, so a matching install is a no-op.
# Local machines are untouched (they use `brew install gitleaks` + `git config core.hooksPath .githooks`).
set -euo pipefail

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || exit 0

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

ci=.github/workflows/ci.yml
url="$(sed -n 's/^  GITLEAKS_URL: *//p' "$ci")"
sha="$(sed -n 's/^  GITLEAKS_SHA: *//p' "$ci")"
want="$(sed -n 's#.*/download/v\([0-9.]*\)/.*#\1#p' <<<"$url")"
if [ -z "$url" ] || [ -z "$sha" ] || [ -z "$want" ]; then
  echo "session-start: could not read the gitleaks pin from $ci" >&2
  exit 1
fi

have="$(gitleaks version 2>/dev/null || true)"
if [ "${have#v}" != "$want" ]; then
  echo "session-start: installing gitleaks $want (was: ${have:-none})" >&2
  sudo_cmd=sudo; [ "$(id -u)" -eq 0 ] && sudo_cmd=""
  SUDO="$sudo_cmd" .github/scripts/install-tool.sh "$url" "$sha" gitleaks >&2
fi

git config core.hooksPath .githooks
echo "session-start: gitleaks $(gitleaks version), core.hooksPath=$(git config core.hooksPath)" >&2
