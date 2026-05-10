# IaC — Flux & Renovate

## Flux CD

Flux is the GitOps operator for K3s. It watches the GitHub repo and continuously reconciles the cluster state toward what's defined in Git. Push a change, Flux applies it. No `kubectl apply`, no manual deployments.

### How it works

```
Developer pushes to GitHub
  → Flux polls repo every 60 seconds
    → Detects new commit
      → Reconciles cluster state
        → Creates/updates/deletes resources as needed
          → Done — cluster matches Git
```

Flux is terminal-native — no UI required. `flux` CLI shows sync status, reconciliation history, and errors.

```bash
# Check status of all Flux resources
flux get all

# Force immediate reconciliation
flux reconcile source git homelab

# Watch logs
flux logs --follow
```

### What Flux manages

Everything in the `k8s/` directory of the repo:

```
k8s/
  flux-system/      ← Flux's own config (bootstrapped, then self-managed)
  infrastructure/   ← MetalLB, Traefik, Authentik, cert-manager, Vault, ESO, AWX
  apps/             ← All application workloads
```

### HelmRelease pattern

Most workloads are deployed as Helm charts via Flux `HelmRelease` resources:

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: outline
  namespace: outline
spec:
  interval: 30m
  chart:
    spec:
      chart: outline
      version: "1.2.3"        # ← Renovate keeps this current
      sourceRef:
        kind: HelmRepository
        name: outline
  values:
    image:
      tag: "0.75.0"           # ← Renovate keeps this current
```

### Dependency ordering

Flux respects dependencies between resources. Infrastructure components (Vault, ESO, cert-manager) are deployed before applications that depend on them, using `dependsOn` in Kustomizations.

## Renovate

Renovate runs as a job in K3s and monitors the repo for outdated dependencies. It opens pull requests automatically when new versions are available.

### What Renovate monitors

- Helm chart versions in `HelmRelease` manifests
- Container image tags in workload manifests
- Terraform provider versions
- Ansible Galaxy role versions

### Workflow

```
Renovate scans repo (scheduled, e.g. nightly)
  → Finds: outline Helm chart 1.2.3 → 1.3.0 available
    → Opens PR: "chore: update outline chart to 1.3.0"
      → Review PR (check changelog, breaking changes)
        → Merge
          → Flux detects new commit
            → Deploys updated chart automatically
```

No manual version tracking. No forgotten updates. Every dependency stays current with human review before deployment.

### Configuration

`renovate.json` in repo root:

```json
{
  "extends": ["config:base"],
  "kubernetes": {
    "fileMatch": ["k8s/.+\\.ya?ml$"]
  },
  "helm-values": {
    "fileMatch": ["k8s/.+/values\\.ya?ml$"]
  },
  "packageRules": [
    {
      "matchUpdateTypes": ["patch"],
      "automerge": true        // patch versions auto-merge if CI passes
    }
  ]
}
```
