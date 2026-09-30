#!/usr/bin/env bash
# .claude/scripts/chart-bump/lib.sh — shared helpers (sourced, not executed).
#
# Isolated helm state: the user's ~/.config/helm repo list can reference caches
# that don't exist (helm then errors on unrelated repos), and background jobs
# must not share /tmp. Everything goes under $CB_TMP.

CB_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
CB_TMP="${CB_TMP:-${CLAUDE_JOB_DIR:+$CLAUDE_JOB_DIR/tmp}}"
CB_TMP="${CB_TMP:-${TMPDIR:-/tmp}/chart-bump}"
mkdir -p "$CB_TMP"
export HELM_REPOSITORY_CONFIG="$CB_TMP/helm-repos.yaml"
export HELM_REPOSITORY_CACHE="$CB_TMP/helm-cache"
export HELM_CONFIG_HOME="$CB_TMP/helm-config"
mkdir -p "$HELM_REPOSITORY_CACHE" "$HELM_CONFIG_HOME"

# cb_repo_url <HelmRepository name>  -> url  (searches every HelmRepository in k8s/)
cb_repo_url() {
  local name="$1" f
  for f in $(grep -rl "kind: HelmRepository" "$CB_ROOT/k8s" 2>/dev/null); do
    yq "select(.kind==\"HelmRepository\" and .metadata.name==\"$name\") | .spec.url" "$f" 2>/dev/null
  done | grep -v -E '^null$|^$' | head -1
}

# cb_chart_ref <chart> <repo-url> -> arguments for helm (repo flag or oci ref)
# usage: helm show chart $(cb_chart_ref traefik https://...)
cb_chart_ref() {
  local chart="$1" url="$2"
  if [[ "$url" == oci://* ]]; then echo "${url%/}/$chart"; else echo "$chart --repo $url"; fi
}
