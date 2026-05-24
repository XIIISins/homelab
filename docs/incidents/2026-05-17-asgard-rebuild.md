<!-- docs/incidents/2026-05-17-asgard-rebuild.md -->

### 2026-05-17 — Asgard rebuild + 9 architectural findings

Deliberate full teardown + rebuild of the asgard K3s cluster to validate end-to-end IaC. Triggered by the realization that the previous cluster had accumulated manual state during incident response (sysctls, iptables-shaped rules, imperative Vault config) that wasn't captured in code. Rebuild proves the IaC works AND surfaces what's missing.

Renamed `must-run` → `asgard` and `can-run` → `jotunheim` in the same arc — see `docs/procedures/teardown-rebuild.md` for the full runbook.

**Process:** state capture → per-tier rename commits (5) → graceful teardown (Vault drain, terraform destroy) → Synology LUN cleanup → terraform apply → ansible play → Flux bootstrap → Vault re-init → workload validation. Total wall-clock ~5 hours.

**Findings, all closed during the same session:**

1. **SealedSecret CRD timing race.** Putting SealedSecret resources alongside the sealed-secrets HelmRelease in the `infrastructure/` Kustomization failed Flux dry-run (CRD doesn't exist at validation time). Fix: split `vault-config/` and `synology-csi-config/` Kustomizations next to the existing `metallb-config/`, all `dependsOn: infrastructure`. Per-component-config pattern formalized.

2. **Sealed-secrets master keys not backed up.** Fresh controller = fresh keypair = old SealedSecrets undecryptable. Both had to be re-sealed from plaintext (vault-unseal from Ansible Vault, synology-csi from 1Password). Master keys now backed up to 1Password.

3. **Vault test KV entry was manual state.** The `secret/ansible/test/hello` entry used by `playbooks/test-vault-lookup.yml` was created by `vault kv put` originally and never captured. Now a `vault_kv_secret_v2` resource in Terraform.

4. **Vault Raft followers don't auto-join cleanly.** vault-1 and vault-2 sat sealed after vault-0 init. Manual `vault operator raft join` on each. Recovery procedure documented.

5. **MetalLB `route_localnet=1` missing.** VIPs unreachable from outside the cluster — kernel dropped packets destined for the VIP because MetalLB only ARPs, doesn't bind. Manual `sysctl` got the cluster working; refactor landed it in `roles/k3s/tasks/network.yml`.

6. **VLAN 20 source-based policy routing missing.** Replies from MetalLB VIPs went out via the default eth0 route (asymmetric) — UCG's stateful firewall dropped them. Manual `ip rule` / `ip route` got it working; landed as `vlan20-policy-routing.service` systemd unit in `network.yml`. OS-independent.

7. **CP rebuild → "duplicate node name."** Destroying/recreating gondul VM and re-running the playbook failed with etcd join error. Fix: `kubectl delete node gondul` from a surviving CP. The K3s native node-delete handler evicts the stale etcd member, allowing the rebuilt VM to join.

8. **CP rebuild of default init node needs override.** If you destroy the node that's `k3s_init_node` (default `gondul`), the role would `--cluster-init` it as a fresh cluster. Override: `-e k3s_init_node=hlokk`.

9. **K3s role install lacks idempotency guard.** Re-running the play against a healthy CP could fire the restart-k3s handler, which causes K3s to re-attempt join, which fails with "duplicate node name." Fix: new `roles/k3s/tasks/detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s` AND node is `Ready`. `install.yml` and `calico.yml` skip when true. **Deeper bug remains:** the restart handler itself isn't safe for existing CP members — a genuine config template change would still trigger duplicate-join. Tracked as separate item.

**Plus:** Vault init can leave a stuck partial state (recovery: delete StatefulSet + PVCs + reconcile). Flux deploy key not in IaC (`flux bootstrap github` re-uses if present, regenerates if not).

**Bonus during the session:** moved Göndul from Urd to Verd (deferred since 2026-05-14), bumped to 2vCPU/4GB after OOM during reconciliation churn. Parameterized CP cpu/memory in the Terraform locals map. Refactored `roles/k3s/tasks/` to split `network.yml` (sysctls + policy routing) from the install path, and `detect-state.yml` for idempotency.

**Resolution:** 9 findings closed in IaC (commits visible in `git log --oneline -20`). Cluster healthy, all workloads recovered, doc sweep on 2026-05-17 captured everything into CLAUDE.md and this doc.

**Root-cause pattern:** every finding was either (a) accumulated manual state that survived in the live cluster but not in Git, or (b) a structural gap that only surfaces during cluster-from-zero (CRD timing, idempotency, init node identity). The pre-rebuild cluster ran fine because none of the gaps fired in steady state. Lesson: deliberate teardown is the only honest test of IaC completeness. Worth doing periodically.

