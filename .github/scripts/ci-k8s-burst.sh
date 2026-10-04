#!/usr/bin/env bash
# .github/scripts/ci-k8s-burst.sh <base-sha> — the burst-cluster test's OFFLINE gates, on every PR that touches k8s/ (10h slice 4).
#
# Runs identically on a laptop and in CI. Needs: kubectl, python3 + PyYAML, git history back to the base.
#
# What it does, with no cluster and no credentials:
#   1. maps the PR's changed files to Flux components (aiops/runner/k8s_burst_plan.py), renders the BURST copy of the tree for them
#      (skipped components removed, Vault one-node, Let's Encrypt cut out, asgard-only storage classes remapped);
#   2. fails if an ExternalSecret reads a Vault path that no Terraform module or scripts/secrets/mirror-map.toml entry declares
#      (the throwaway Vault is seeded FROM the PR, so only this inventory check can catch a typo'd path);
#   3. fails if any directory Flux would reconcile no longer builds with `kubectl kustomize`;
#   4. checks that every container image named in the rendered app manifests exists upstream (an image that was never published is an outage,
#      see .claude/scripts/chart-bump/images-exist.sh; HelmRelease images are not visible here, the live burst test catches those).
# What it does NOT do: apply anything. That is the burst test itself (scripts/burst/k8s-pr-test), run on Frigg for agent PRs.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
base="${1-}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

scope=(--diff-base "$base")
# A push to main (or a new branch) has no usable base: check the whole burst copy instead of guessing.
if [ -z "$base" ] || [[ "$base" =~ ^0+$ ]]; then scope=(--all); fi
python3 aiops/runner/k8s_burst_run.py --repo . --work "$tmp/work" --out "$tmp" --offline "${scope[@]}"

if [ -f "$tmp/work/k8s/asgard/apps/kustomization.yaml" ] && grep -q '^  - ' "$tmp/work/k8s/asgard/apps/kustomization.yaml"; then
  kubectl kustomize "$tmp/work/k8s/asgard/apps" > "$tmp/apps.yaml"
  if grep -qE '^\s+(- )?image:' "$tmp/apps.yaml"; then
    .claude/scripts/chart-bump/images-exist.sh "$tmp/apps.yaml"
  else
    echo "no container images in the touched app manifests"
  fi
else
  echo "no app components touched: image check skipped"
fi
