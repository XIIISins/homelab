<!-- docs/plans/active/10h-k8s-burst-test.md -->

# 10h — burst-cluster tests for `k8s/` pull requests

*Drafted 2026-10-04 at the operator's request ("throwaway Vault, and otherwise as close to our env as possible"). Parent plan: [`10h-predictive-change.md`](10h-predictive-change.md) (the "Tested on" table, row `k8s/**`). Substrate: [`procedures/burst-substrate.md`](../../procedures/burst-substrate.md). Status: see [`plans/README.md`](../README.md).*

## What it proves, and what it cannot

A PR that edits `k8s/` is applied to a throwaway K3s that looks like asgard and checked for: manifests accepted by the API server, Flux kustomizations Ready, HelmReleases installed, ExternalSecrets resolved (so a renamed Vault key or property is caught), pods Ready, and the touched workload's own smoke check. It **cannot** prove anything that depends on state the burst cluster does not have: real data, the Synology iSCSI/NFS back ends, MetalLB L2 on VLAN 20, the Cloudflare tunnel, Authentik users, real SMTP/OIDC. Those components are skipped (below) and the PR summary says exactly what was skipped, so a green test never reads as "safe in prod".

## Fidelity rules ("as close to our env as possible")

1. **Same Flux, same manifests.** Real Flux controllers on the burst cluster, a `GitRepository` on the PR's head commit, and Kustomizations generated from the repo's own `k8s/asgard/flux-system/*.yaml` (same `dependsOn` order: infrastructure, then config, then apps). Nothing is hand-applied with `kubectl apply`.
2. **Same Vault shape, throwaway content.** A single-node Vault from the SAME chart and version as `k8s/asgard/infrastructure/vault`, Shamir-unsealed by the harness (no AWS KMS, no prod key), the same `secret/` KV v2 mount, the same Kubernetes auth role `eso` and policy, TLS from the same cert-manager internal CA. ESO and the `vault` ClusterSecretStore are the unmodified manifests.
3. **Seeded from the PR itself.** The harness scans every `ExternalSecret` in the tree, collects each `remoteRef` (`key` + `property`, or `dataFrom.extract`) and writes a **random** value at that path. A PR that points at a path or property that does not exist in prod's naming still passes here (the seed is derived from the PR), so the harness also checks the referenced paths against the **committed Vault path inventory** (`terraform/vault` KV declarations and `docs/architecture/identity-secrets.md`) and fails the PR on an unknown path. No prod secret is ever read, copied or mentioned.
4. **SealedSecrets** cannot be decrypted without the cluster key, so each is replaced by a plain Secret of the same name, namespace and key names with random values (key names are plaintext in `spec.encryptedData`).
5. **Skipped on purpose, listed in the summary:** `metallb` + `metallb-config` (no L2 VLAN), `synology-csi` + `synology-csi-config` + `csi-driver-nfs` (no Munin; `synology-csi-iscsi-retain-vol2` and `nfs-client` are remapped to `local-path`), `cloudflared`, `tailscale`, `sealed-secrets` itself (replaced by rule 4), and the AWS-KMS unseal secret. `traefik` and the Gateway API stay (routes are the thing PRs change); the Gateway's `LoadBalancer` becomes a NodePort.
6. **Same versions.** The burst cluster is built by the `k3s` role at the pinned `k3s_version`, with prod's Calico version; pod and service CIDRs differ (burst-substrate keeps them off prod's) and the harness records that difference.

## The flow

`/aiops draft` kind `k8s` -> author drafts -> PR opens -> the Toolbelt reads the PR (read-only, like `pr-canary-test`), runs the offline gates (`render-diff.sh`, `images-exist.sh`, kubeconform; stdlib + the helpers already in `.claude/scripts/chart-bump/`), and **proposes `pr-burst-test`** -> the operator approves (cost ~$0.11/h, TTL 4 h) -> the **burst runner on Frigg** (same shape as the rebuild runner: a unix socket, one op at a time, never a free-form command) does: `burst-up` -> install Flux + Vault + seeds -> reconcile the PR commit -> checks -> collect a summary and the full log -> `burst-down` (always, in a `finally`, and the Frigg reaper is the second guard). The summary goes into the PR description; the full output goes to the private Discord thread.

## Slices

| # | Slice | Status |
|---|---|---|
| 1 | **Planning logic**: scan `k8s/` for ExternalSecrets/SealedSecrets, the seed plan, the skip list and storage remap, map a PR's changed paths to the components to check. Pure Python, unit-tested, no network. `aiops/runner/k8s_burst_plan.py` | built 2026-10-04 |
| 2 | **Harness** `scripts/burst/k8s-pr-test <branch>`: runs on Frigg by hand first (operator-approved cost), proving fidelity on `main` with no changes (baseline must be green) and on a deliberately broken branch (must fail for the right reason). Built: offline gate first (render, Vault path inventory, `kubectl kustomize` of every Flux path), cluster = 1 control plane + 3 workers from `burst-up`, Flux + same Vault chart (one node, Shamir) + random seeds, diagnostics captured before teardown, `trap` teardown. | built 2026-10-04 |
| 3 | **Author class `k8s`** (narrow: `k8s/asgard/apps/<one app>/**`), `pr-burst-test` registry action, Toolbelt proposal on PR open, runner socket. Built: class `k8s` (enabled, `burst_test`), `pr-burst-test` (runner-only, `semaphore.applied: false` until deployed), `pr_test.inspect_k8s_pr`, the Toolbelt's `burst_exec` client, `aiops/runner/burst_runner.py` (its own service, Vault AppRole, private memory-only ssh-agent, harness always from a clean main checkout), role `aiops-burst-runner` + `asgard-burst-runner.yml` + `terraform/vault/burst-runner.tf`. **Deployed 2026-10-05** (Vault identity, seeded env, runner on Frigg; units, `0660` socket, peer check and the loaded credentials verified; `pr-burst-test` applied, Toolbelt socket set). | live 2026-10-05; acceptance passed on PRs #174 (pass) and #177 (offline-gate fail); Frigg reboot test pending |
| 4 | Offline gates in CI: `.github/scripts/ci-k8s-burst.sh` in the `kubernetes` job (burst copy of the touched components builds, Vault path inventory, images in the touched app manifests exist) on every k8s PR. | built 2026-10-04 (PR #170) |

## Guardrails (unchanged from the parent plan)

One burst cluster per PR, TTL 4 h, at most one test running fleet-wide, the kill switch and maintenance flag apply, burst has no route to prod (`tag:burst` is not a source of any grant), the author never holds cloud credentials, and a human merges (merging `k8s/` IS the deploy).

## Header status history

*The status line this plan carried in its header, moved here verbatim when status consolidated into [`plans/README.md`](../README.md) (2026-10-10). It is a dated snapshot, not current status.*

> Status: **slices 1-2 built and run live (2026-10-04)**: the baseline on unchanged `main` passes in about 2.5 minutes on a cluster built from Terraform + Ansible; the broken-branch cases fail for the right reasons (results in [`procedures/k8s-burst-test.md`](../../procedures/k8s-burst-test.md)). Slices 3-4 are open.
