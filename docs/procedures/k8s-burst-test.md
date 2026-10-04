<!-- docs/procedures/k8s-burst-test.md -->

# Procedure — test a `k8s/` change on a burst cluster

*Phase 10h ([design](../operations/10h-k8s-burst-test.md)). Code: `scripts/burst/k8s-pr-test`, `aiops/runner/k8s_burst_run.py`, `aiops/runner/k8s_burst_plan.py`; the cluster itself is the existing [burst substrate](burst-substrate.md) (`scripts/burst/burst-up` / `burst-down`, Terraform + the `burst-k3s.yml` playbook). Run it on **Frigg** (tailnet reach to the burst API, Vault env, the DO token).*

## Run it

```bash
# On Frigg, as ghost, from a checkout of this repo (the script loads the Vault-backed env itself).
scripts/burst/k8s-pr-test                                   # BASELINE: origin/main, platform core only (~10 min, ~$0.11/h)
scripts/burst/k8s-pr-test --ref feat/some-branch --only netbox   # a branch, plus the component it touches
scripts/burst/k8s-pr-test --ref <sha> --only outline --keep      # leave the cluster up to poke at it (the TTL reaper still applies)
scripts/burst/k8s-pr-test --reuse                                # use an already-built burst cluster (Vault must be uninitialised)
```

Exit 0 = passed. `~/.cache/homelab/burst/k8s-test/<sha>/summary.md` is what goes into a PR description; `summary.json` has the detail. The script always runs `burst-down` on the way out (a `trap`), unless `--keep`/`--reuse`; if teardown fails it says so, and the Frigg reaper (TTL, default 3 h) is the backstop. `scripts/burst/burst-down --yes` by hand is always safe.

## What happens

1. `guard`: every node must be named `burst-N`, or nothing is applied.
2. `render`: a COPY of `k8s/asgard` is made with the burst differences (skipped components removed, Vault one-node/Shamir, Traefik NodePort and one replica, Let's Encrypt cut out, asgard-only storage classes remapped). The full list is in the summary.
3. `flux`: the repo's own `gotk-components.yaml`. `infra`: `kubectl kustomize` of the copy, applied server-side with retries until the CRDs from the HelmReleases exist (what Flux's retry loop does). `certs`: `cert-manager-config` (internal CA, the Vault serving certificate).
4. `vault`: init (1 share), unseal, the same KV v2 / Kubernetes auth / `eso` policy and role as `terraform/vault/main.tf`, then **one random secret per `ExternalSecret` reference found in the tree**. A unit test holds the policy equal to Terraform's; a second checks every seeded path exists in the committed inventory (`scripts/secrets/mirror-map.toml` and Terraform names), so a typo'd Vault path cannot pass here and fail in prod.
5. `config`, `apps`, `wait`, `checks`: the ClusterSecretStore, Gateways and the selected apps are applied; HelmReleases, ExternalSecrets and workloads are collected (names and booleans only) and judged. Components that cannot be ready without something outside the cluster are waived BY NAME in `EXPECTED_NOT_READY` and still printed.

## Gotchas found building it

- **A DigitalOcean VPC drops unencapsulated pod traffic.** With the role's default `VXLANCrossSubnet`, nodes on one subnet route pod IPs directly and the VPC fabric discards them: nodes were `Ready`, Calico pods `Running`, and every cross-node pod flow (CoreDNS, the API, Flux fetching Helm indexes) timed out. The burst inventory sets `k3s_calico_encapsulation: VXLAN`; prod keeps the default.
- **Traefik's LoadBalancer Service never gets an address without MetalLB**, and the chart's install waits on it until its 10-minute timeout: the burst copy uses a NodePort.
- **`kubectl exec` appends notices after a command's JSON**, so Vault output is parsed with `raw_decode`, not `json.loads`.
- **Three 4 GB droplets cannot hold all of asgard**: the default run installs only the platform core plus the components you name with `--only`.

## What a green run does NOT prove

It does not exercise the Synology iSCSI/NFS back ends, MetalLB, Cloudflare tunnels, real data, Authentik users or any dependency outside the cluster (NetBox's Postgres VIP, for one). The summary lists what was not installed.
