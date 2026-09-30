#!/usr/bin/env bash
# inventory.sh — every Flux-managed chart: pinned version vs latest upstream.
#
#   .claude/scripts/chart-bump/inventory.sh            # table
#   .claude/scripts/chart-bump/inventory.sh --live     # also show what the cluster runs (needs KUBECONFIG)
#
# Read-only. "latest" = newest version in the chart repo index / OCI registry
# (helm show chart). Pre-releases are not filtered by OCI show; eyeball them.
set -u
. "$(dirname "$0")/lib.sh"
LIVE=0; [[ "${1:-}" == "--live" ]] && LIVE=1

printf '%-26s %-22s %-18s %-18s %s\n' RELEASE CHART PINNED "LATEST (app)" STATUS
for f in $(grep -rl "kind: HelmRelease" "$CB_ROOT/k8s" 2>/dev/null | sort); do
  yq 'select(.kind=="HelmRelease") | .metadata.name + " " + (.spec.chart.spec.chart // "-") + " " + (.spec.chart.spec.version // "-") + " " + (.spec.chart.spec.sourceRef.name // "-")' "$f" 2>/dev/null
done | while read -r rel chart ver src; do
  if [[ "$src" == "-" || "$ver" == "-" || "$ver" == "null" ]]; then
    printf '%-26s %-22s %-18s %-18s %s\n' "$rel" "$chart" "${ver:-git}" "-" "git-sourced chart (check tag manually)"; continue
  fi
  url=$(cb_repo_url "$src")
  [[ -z "$url" ]] && { printf '%-26s %-22s %-18s %-18s %s\n' "$rel" "$chart" "$ver" "?" "no HelmRepository '$src' found"; continue; }
  # shellcheck disable=SC2046
  out=$(helm show chart $(cb_chart_ref "$chart" "$url") 2>/dev/null)
  latest=$(echo "$out" | awk '/^version:/{print $2}' | head -1)
  app=$(echo "$out" | awk '/^appVersion:/{print $2}' | head -1)
  v_pin="${ver#v}"; v_lat="${latest#v}"
  if [[ -z "$latest" ]]; then st="lookup failed"
  elif [[ "$v_pin" == "$v_lat" ]]; then st="current"
  else st="BEHIND"; fi
  printf '%-26s %-22s %-18s %-18s %s\n' "$rel" "$chart" "$ver" "$latest ($app)" "$st"
done

if [[ $LIVE -eq 1 ]]; then
  echo; echo "--- live HelmReleases"; kubectl get hr -A --no-headers 2>/dev/null | awk '{print $1"/"$2" ready="$3" "$4" "$5" "$6" "$7" "$8" "$9}' | cut -c1-170
fi
