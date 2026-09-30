#!/usr/bin/env bash
# platform.sh — versions that are NOT plain chart pins: pinned server images, Flux, K3s, Calico.
#
#   .claude/scripts/chart-bump/platform.sh           # needs network; KUBECONFIG optional (adds "running" column)
#
# Read-only.
set -u
. "$(dirname "$0")/lib.sh"
gh_latest() { curl -s "https://api.github.com/repos/$1/releases/latest" | yq -p json '.tag_name' 2>/dev/null; }

echo "== Vault server image (pinned explicitly; the chart default moves with the chart)"
pin=$(yq 'select(.kind=="HelmRelease") | .spec.values.server.image.tag' "$CB_ROOT/k8s/asgard/infrastructure/vault/helmrelease.yaml" 2>/dev/null | grep -v -E '^null$|^$|^---$' | head -1)
lat=$(curl -s "https://hub.docker.com/v2/repositories/hashicorp/vault/tags?page_size=40&ordering=last_updated" | yq -p json '.results[].name' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1)
echo "   pinned: ${pin:-?}   latest on Docker Hub: ${lat:-?}"

echo "== Flux"
inst=$(grep -m1 "Flux Version" "$CB_ROOT/k8s/asgard/flux-system/flux-system/gotk-components.yaml" 2>/dev/null | sed 's/.*: //')
echo "   in repo (gotk-components.yaml): ${inst:-?}   latest: $(gh_latest fluxcd/flux2)   local CLI: $(flux version --client 2>/dev/null | awk '/flux:/{print $2}')"
echo "   NOTE: read the release notes' Kubernetes compatibility table — minimum K8s rises with each Flux minor."

echo "== K3s"
cur=$(grep -E '^k3s_version:' "$CB_ROOT/ansible/roles/k3s/defaults/main.yml" | sed -E 's/.*"(.*)".*/\1/')
chan=$(curl -s https://update.k3s.io/v1-release/channels | yq -p json '.data[] | select(.name=="stable" or .name=="latest") | .name + "=" + .latest' | tr '\n' ' ')
echo "   role default: ${cur:-?}   channels: $chan"
if command -v kubectl >/dev/null 2>&1 && [[ -n "${KUBECONFIG:-}" ]]; then
  echo "   running: $(kubectl get nodes --no-headers 2>/dev/null | awk '{print $1"="$5}' | tr '\n' ' ')"
fi
echo "   Kubernetes forbids skipping minors: plan one k3s-upgrade.yml run per minor (see docs/procedures/k3s-upgrade.md)."

echo "== Calico (NOT Flux-managed; addon manifest pinned in the k3s role)"
echo "   role default: $(grep -E '^calico_version:' "$CB_ROOT/ansible/roles/k3s/defaults/main.yml" | sed -E 's/.*"(.*)".*/\1/')   latest 3.x patches: $(curl -s 'https://api.github.com/repos/projectcalico/calico/releases?per_page=30' | yq -p json '.[] | select(.prerelease == false) | .tag_name' | sort -V | awk -F. '{k=$1"."$2; l[k]=$0} END{for(k in l) print l[k]}' | sort -V | tail -3 | tr '\n' ' ')"
if command -v kubectl >/dev/null 2>&1 && [[ -n "${KUBECONFIG:-}" ]]; then
  echo "   running: $(kubectl get installation default -o jsonpath='{.status.calicoVersion}' 2>/dev/null)"
fi

echo "== Terraform providers with server-version coupling"
for d in authentik vault netbox; do
  echo "   $d: $(grep -h -A2 'source' "$CB_ROOT/terraform/$d/versions.tf" 2>/dev/null | grep -E 'version' | head -2 | tr -s ' ' | tr '\n' ' ')"
done
echo "   (provider must track the server: authentik provider = server release; e-breuninger/netbox tops out below NetBox's newest minor; vault provider 5.x needs Terraform >= 1.11)"
