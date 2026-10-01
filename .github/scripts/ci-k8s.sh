#!/usr/bin/env bash
# .github/scripts/ci-k8s.sh — render every kustomization under k8s/ and
# schema-validate the output. Runs identically on a laptop and in CI.
#
# Needs: kubectl (for `kubectl kustomize`) + kubeconform on PATH.
#
# What it catches: broken kustomization references, duplicate/missing
# resources, YAML that renders but is not a valid K8s object, and CRD
# objects that violate their published schema (datreeio CRDs-catalog).
# What it does NOT catch: Helm values mistakes (HelmRelease values are opaque
# to schemas), Flux ${var} substitution gaps, and anything that only fails
# against the live cluster (admission webhooks, ownership conflicts).
#
# CRDs with no published schema are skipped (-ignore-missing-schemas) rather
# than failed: the catalog does not cover every operator in the fleet.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'
# Vendored upstream manifests that are not ours to lint.
SKIP_RE='^k8s/asgard/flux-system/flux-system$'

fail=0
rendered=0
resources=0
# kubeconform ignores files without a .yaml/.yml/.json extension — silently.
tmp="$(mktemp --suffix=.yaml)"
trap 'rm -f "$tmp" "$tmp.err"' EXIT

while IFS= read -r dir; do
  [[ "$dir" =~ $SKIP_RE ]] && continue
  if ! kubectl kustomize "$dir" >"$tmp" 2>"$tmp.err"; then
    echo "::error title=kustomize build failed::$dir: $(head -c 400 "$tmp.err" | tr '\n' ' ')"
    fail=1
    continue
  fi
  rendered=$((rendered + 1))
  rc=0
  out="$(kubeconform -strict -ignore-missing-schemas -summary \
        -schema-location default -schema-location "$CRD_SCHEMAS" "$tmp" 2>&1)" || rc=$?
  n="$(sed -n 's/^Summary: \([0-9]*\) resources found.*/\1/p' <<<"$out")"
  resources=$((resources + ${n:-0}))
  if [ "$rc" -ne 0 ]; then
    echo "::error title=kubeconform failed::$dir"
    echo "$out" | sed "s|^|[$dir] |"
    fail=1
  fi
done < <(find k8s -name kustomization.yaml -exec dirname {} \; | sort)

# Guard against the silent-pass failure mode: a validator that saw nothing.
if [ "$resources" -eq 0 ]; then
  echo "::error title=no resources validated::kubeconform saw 0 resources — script is broken"
  exit 1
fi
echo "rendered $rendered kustomizations, schema-checked $resources resources"
exit "$fail"
