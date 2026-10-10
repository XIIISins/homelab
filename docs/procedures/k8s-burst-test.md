<!-- docs/procedures/k8s-burst-test.md -->

# Procedure — test a `k8s/` change on a burst cluster

*Phase 10h ([design](../plans/active/10h-k8s-burst-test.md)). Code: `scripts/burst/k8s-pr-test`, `aiops/runner/k8s_burst_run.py`, `aiops/runner/k8s_burst_plan.py`; the cluster itself is the existing [burst substrate](burst-substrate.md) (`scripts/burst/burst-up` / `burst-down`, Terraform + the `burst-k3s.yml` playbook). Run it on **Frigg** (tailnet reach to the burst API, Vault env, the DO token).*

## Run it

```bash
# On Frigg, as ghost, from a checkout of this repo (the script loads the Vault-backed env itself).
scripts/burst/k8s-pr-test                                   # BASELINE: origin/main, platform core only (~10 min, ~$0.11/h)
scripts/burst/k8s-pr-test --ref feat/some-branch --only netbox   # a branch, plus the component it touches
scripts/burst/k8s-pr-test --ref <sha> --only outline --keep      # leave the cluster up to poke at it (the TTL reaper still applies)
scripts/burst/k8s-pr-test --reuse                                # use an already-built burst cluster (Vault must be uninitialised)
```

Exit 0 = passed. `~/.cache/homelab/burst/k8s-test/<sha>/summary.md` is what goes into a PR description; `summary.json` has the detail. The script always runs `burst-down` on the way out (a `trap`), unless `--keep`/`--reuse`; if teardown fails it says so, and the Frigg reaper (TTL, default 3 h) is the backstop. `scripts/burst/burst-down --yes` by hand is always safe.

## For agent PRs: the automatic path (slice 3, built 2026-10-04; deploy below)

`/aiops draft` kind **k8s** files a change request for ONE app under `k8s/asgard/apps/<app>/` (class `k8s`, `aiops/author-classes.yml`). **Test before PR (since 2026-10-10):** the class has `test_before_pr`, so the dispatcher pushes the branch and the PR opens only
afterwards: when the test has passed (the result is in the description from the start) or cannot run (the PR says "Not tested" and why). A FAILED test opens no PR: the
request fails with the reason in its Discord thread and the branch is kept three days for inspection, then deleted. Approving the request on its card also approves ONE
sha-pinned test of the drafted branch, so there is no second card to press. The Toolbelt
reads the branch from GitHub itself (`pr_test.inspect_k8s_pr`: yaml under exactly one app, added or modified only, no `hostPath`/`privileged`/`hostNetwork`/cluster-wide
objects) and **proposes `pr-burst-test`**; you approve the card; the **burst runner** (`aiops/runner/burst_runner.py`, a separate service on Frigg with its own Vault
identity) fetches the branch, checks the head is still the approved commit, runs `scripts/burst/k8s-pr-test` **from its own clean checkout of main (never the PR's
code)** and tears the cluster down. The runner's markdown summary replaces the PR description's `## Burst-cluster test` section. A passed test does not merge anything:
a human reads the diff and the summary and merges (merging IS the deploy, Flux). CI's offline half of the same gates (`.github/scripts/ci-k8s-burst.sh`: Vault path
inventory, the burst copy builds, images exist) runs on EVERY k8s PR, agent or not (slice 4).

## Deploy the runner (operator steps; the role is OFF until you do these)

1. **Merge** the PR carrying the runner, then `terraform apply` in `terraform/vault` (main checkout): the `aiops-burst-runner` policy and AppRole.
2. **State identity + seed.** `terraform apply` in `terraform/aws` (Bootstrap identity, main checkout): the plan adds only `aws_iam_user.burst_runner_state`, its access key and
   an inline policy (`terraform/aws/burst-runner.tf`: s3 get/put/delete on `digitalocean-burst/terraform.tfstate` and its `.tflock`, `ListBucket` on that prefix, nothing else).
   Then `scripts/secrets/seed-burst-state-key` (Vault shim loaded) writes `secret/ansible/aiops/burst/env`: the AWS key from the Terraform outputs and `digitalocean_token` copied
   from `secret/ansible/frigg/iac-env` (the shared custom-scope token; scopes in [`burst-substrate.md`](burst-substrate.md)). It prints only "ok" lines and hash-verifies the read-back;
   `--check` re-verifies read-only. Not mirrored to 1Password on purpose (the DO token's source of truth is `iac-env`; the AWS key is re-minted by Terraform).
3. **Mint a SecretID** (`vault write -f auth/approle/role/aiops-burst-runner/secret-id`) and install the role from the main checkout:
   `ansible-playbook playbooks/asgard-burst-runner.yml -e aiops_burst_runner_enabled=true -e aiops_burst_runner_role_id=... -e aiops_burst_runner_secret_id=...`
   (installs the user, the credential loader, a private memory-only ssh-agent for the fleet key and the runner; checks the socket is `0660`, group `aiops-burst-clients`).
4. **Connect the Toolbelt**: set `aiops_toolbelt_burst_socket: /run/aiops-burst/runner.sock` (and add the three `ansible/roles/aiops-toolbelt` units' group via the role), flip
   `semaphore.applied` to `true` for `pr-burst-test` in `aiops/actions.yml` by PR, and let the Toolbelt deploy.
5. **Reboot-test Frigg** (CLAUDE.md "Persistence validation"): the units, the tmpfs credentials and the agent must come back by themselves.
6. **Acceptance**: file a k8s draft for a harmless change to a baseline-green app (e.g. a label on `apex-static`), approve its card, approve the test card; the PR gains a
   `## Burst-cluster test` section. Then a deliberately broken one (a typo'd Vault path fails at the offline gate in seconds; a bad image tag fails on the cluster).

Until step 4 the class works but its PRs say "Not tested: ... the executor/runner is not applied yet (operator gate)", which is the safe default. Renew the SecretID before
90 days (calendar it with the other Frigg-side roles).

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
- **A new author class that gets a PR test must be added to the Toolbelt's GitHub read proxy allowlist** (`aiops/toolbelt/github_read_proxy.py`; the Toolbelt unit cannot reach the internet). The first live run said "Not tested: could not read the branch head from GitHub (HTTP 404)" for every retry until `k8s` was added; it happened again with `rightsizing` (2026-10-10: change request 24's PR said HTTP 404 and was merged untested), because the test's class list was kept by hand; the test now reads every class with `canary_test` or `burst_test` from `aiops/author-classes.yml`, so a new tested class cannot be forgotten. The role restarts the proxy when its code changes (it did not before 2026-10-05, so the deploy alone left the old allowlist running).
- **Three 4 GB droplets cannot hold all of asgard**: the default run installs only the platform core plus the components you name with `--only`.

## What a green run does NOT prove

It does not exercise the Synology iSCSI/NFS back ends, MetalLB, Cloudflare tunnels, real data, Authentik users or any dependency outside the cluster (NetBox's Postgres VIP, for one). The summary lists what was not installed.

## Proven live (2026-10-04)

| Case | Result |
|---|---|
| `main`, platform core only | PASSED in 137 s (cluster built from Terraform + Ansible, 1 control plane + 3 workers) |
| `main`, `--only apex-static` | PASSED (the workload was waited for, not just the HelmReleases) |
| `main`, `--only startpage` | fails on the baseline: its init container clones a private repo with a deploy key, so it is waived by name with that reason (the ExternalSecret DID sync from the throwaway Vault) |
| branch with a typo'd Vault path (`k8s/netbox/appp`), `--only netbox` | offline gate FAILED in seconds, no cluster built, names the unknown path |
| agent PR #174 end to end (2026-10-05): `/aiops draft` k8s (a label on `apex-static`) -> PR -> `pr-burst-test` card (proposal 33) -> approved -> runner | PASSED in 458 s, the PR description gained the `## Burst-cluster test` section (what was installed, what was not, phases), the cluster was gone afterwards (no `burst` droplets) |
| agent PR #177 (2026-10-05): a rename of the microbin ExternalSecret's Vault key to a path that does not exist | FAILED in 5 s at the offline gate (no cluster built), the PR description named `k8s/microbin/admin-credentials`; no droplets left. (CR 12, which asked the agent to plant a typo on purpose, made no change: the author declines deliberate breakage, so use a plausible wrong request for failure cases.) |
| branch with a nonexistent cert-manager chart version | FAILED after the wait with `InvalidChartReference ... no cert-manager chart with version matching v99.99.99` plus the cascade (trust-manager, vault PVC, vault-tls) |
