# Teardown & rebuild — must-run K3s → asgard

*Last updated: 2026-05-17 — validated end-to-end this same day*

The first deliberate end-to-end rebuild of the production K3s cluster. Combines a directory/naming rename (`must-run` → `asgard`, `can-run` → `jotunheim`) with a clean teardown and rebuild from IaC. Validates that the IaC is complete and the rebuild path actually works.

**Validation outcome (2026-05-17):** rebuild completed end-to-end. Surfaced 9 architectural gaps (CRD timing, master-keys backup, route_localnet, policy routing, idempotency guard, etcd member cleanup, init-node override, Raft auto-join, stuck-init recovery). All fixed in IaC during the same session. See `homelab-design.md` § Incident log § 2026-05-17 for the full narrative. This document was updated post-rebuild to bake those recoveries into the runbook so the next rebuild doesn't re-discover them.

## Scope

**In scope:**
- Must-run K3s VMs (2001–2003 CPs, 2101–2103 workers) — destroy and recreate
- All `k8s/must-run/` Flux manifests → `k8s/asgard/`
- All Ansible group names, playbook filenames, role references
- All Terraform module directories (`must-run-k3s/`, `must-run-lxcs/`) — rename only, LXC module not destroyed
- All documentation references
- UCG-Ultra VLAN names (`HL-CORE-*`, `HL-CR-*` → `HL-ASG-*`, `HL-JOT-*`)

**Out of scope:**
- Factorio LXC (1120) — directory rename only, LXC keeps running
- AdGuard Home LXCs (Saga/Mimir/Kvasir) — not touched
- PBS LXC — not touched
- Synology (Munin) — not touched, but old iSCSI LUNs need cleanup post-rebuild
- KPN Experia Box and UCG-Ultra config — VLAN labels updated, firewall rules unchanged
- Public DNS zone `midgard.xiiisins.com` — not renamed (separate from K3s cluster names)
- `docs/outline/` content quality — being deleted entirely (regenerable from `homelab-design.md` later)

## Naming target

| Old | New |
|---|---|
| `must-run` (kebab-case, paths/text) | `asgard` |
| `Must-run` (capitalized prose) | `Asgard` |
| `must_run` (snake_case, Ansible groups) | `asgard` |
| `can-run` | `jotunheim` |
| `Can-run` | `Jotunheim` |
| `HL-CORE-*` (UCG VLAN names) | `HL-ASG-*` |
| `HL-CR-*` | `HL-JOT-*` |
| `~/.kube/niflheim-must-run.yaml` | `~/.kube/niflheim-asgard.yaml` |

Norse VM/node names (Niflheim, Urd/Verd/Skuld, Göndul/Hlökk/Sigrún, Einherjar-*, Munin) stay — those are identity, not cluster role.

## Arc

```
1. Pre-rebuild state capture (read-only)
2. Per-tier rename commits in Git (no infra changes)
3. Pre-teardown final checks (verify Git is consistent)
4. Teardown — terraform destroy on K3s cluster
5. Rebuild — terraform apply → Ansible → Flux bootstrap → Vault config + KV restore
6. Verification checklist
7. Rollback notes
```

Per-tier commits in Section 2 don't touch running infrastructure — they're text changes only. The teardown in Section 4 is what actually destroys things.

## Session pause points

The arc is designed to be stoppable at clean boundaries. Natural pause points:

| After section | State | Safe to walk away? |
|---|---|---|
| End of Section 1 | State captured, infra untouched | ✅ Indefinitely |
| End of Section 2 (any tier) | Git renamed for completed tiers; remaining tiers untouched; infra untouched | ✅ Indefinitely. Note last completed tier. |
| End of Section 2 (all tiers) | Git fully renamed; infra still on old naming | ✅ Indefinitely. Repo is in "renamed but not yet rebuilt" state — Terraform plan on asgard-k3s/ would show full destroy+create, but you don't run it yet. |
| End of Section 3 | Final checks passed; ready to destroy | ✅ Same as above |
| Mid Section 4 (after destroy, before apply) | **K3s gone, nothing rebuilt yet** | ⚠️ Only for short breaks (lunch). Don't sleep on this state. Asgard services unavailable. |
| Mid Section 5 (Ansible playbook running) | **Partial cluster bring-up** | ❌ Don't pause here. Let the playbook finish or revert. |
| End of Section 5.6 (Vault terraform applied) | Vault has fresh init + TF config | ✅ For short breaks. KV data not restored yet. |
| End of Section 5 | Full rebuild done | ✅ Indefinitely |

**Two-day plan (you have ~2h tonight + Sunday morning):**
- **Tonight:** Sections 1 + 2. State capture (Section 1) is methodical, ~30 min. Rename commits (Section 2) is mechanical, ~30–60 min. End with all rename commits in Git, nothing applied.
- **Tomorrow morning:** Section 3 + 4 + 5 + 6. Teardown + rebuild + verify is ~1.5–2.5h end-to-end. Plenty of slack for Vault re-init faffing and KV restore.

**End-of-tonight handoff note** (write into your scratch / commit message of last rename commit):
```
Stopped after Section 2 — all rename commits in Git.
Tomorrow: resume at Section 3 (pre-teardown checks), then teardown.
State capture is at ~/homelab-rebuild-state/.
Vault root token in 1Password (verified working tonight in Section 1.3).
```

---

## Section 1 — Pre-rebuild state capture

All read-only. Output is captured for reference and recovery.

### 1.1 Repo state

```fish
cd ~/Dev/xiiisins/homelab
git status
git log --oneline -10
git stash list  # should be empty
```

Verify clean working tree, recent commits look right, no leftover stashes.

### 1.2 Terraform plan currency

Each module must show "no changes" before teardown — otherwise drift is mixed in.

```fish
cd ~/Dev/xiiisins/homelab/terraform/proxmox/must-run-k3s
terraform plan -detailed-exitcode
# Exit 0 = no changes (good)
# Exit 2 = drift — investigate before proceeding

cd ~/Dev/xiiisins/homelab/terraform/proxmox/must-run-lxcs
terraform plan -detailed-exitcode

cd ~/Dev/xiiisins/homelab/terraform/vault
terraform plan -detailed-exitcode  # needs VAULT_TOKEN set
```

### 1.3 Vault recovery state confirmation

You have root token and recovery keys in 1Password. Confirm the root token still works:

```fish
env VAULT_ADDR=http://10.0.20.11:8200 VAULT_TOKEN=<root-from-1Password> vault status
```

Expected output: `Initialized: true`, `Sealed: false`. If "permission denied", the root token has been rotated since you captured it — generate a new root via:

```fish
vault operator generate-root -init  # gives you nonce + OTP
# Then for each recovery key (need 3):
vault operator generate-root  # paste recovery key
# Final command produces encoded token; decode with the OTP from step 1
```

Update 1Password with the new root token before proceeding.

### 1.4 Vault KV inventory

Capture all KV data — this is the only Vault state not in IaC.

```fish
set -x VAULT_ADDR http://10.0.20.11:8200
set -x VAULT_TOKEN <root-from-1Password>

vault kv list secret/
vault kv list secret/ansible/
vault kv list secret/ansible/sftpgo/
```

For each entry found, capture the value:

```fish
mkdir -p ~/homelab-rebuild-state
cd ~/homelab-rebuild-state

vault kv get -format=json secret/ansible/sftpgo/admin-password \
    > sftpgo-admin-password.json
```

Currently only one entry (`sftpgo/admin-password`). If new entries have been added since, list them above and capture each.

### 1.5 Vault config snapshot (sanity check that TF matches reality)

```fish
cd ~/homelab-rebuild-state

vault auth list -format=json > auth-methods.json
vault policy list > policies.txt
for p in (vault policy list | grep -v '^root$\|^default$')
    echo "=== Policy: $p ==="
    vault policy read $p
end > policies-content.txt

vault list -format=json auth/approle/role > approle-roles.json
vault read -format=json auth/approle/role/ansible-local > approle-ansible-local.json
vault read -format=json auth/approle/role/ansible-awx > approle-ansible-awx.json
vault read -format=json auth/kubernetes/config > k8s-auth-config.json
vault list -format=json auth/kubernetes/role > k8s-roles.json
vault read -format=json auth/kubernetes/role/eso > k8s-role-eso.json
```

Compare against `terraform/vault/main.tf` to confirm IaC matches live state.

### 1.6 Cluster resource snapshot

```fish
cd ~/homelab-rebuild-state

kubectl get all -A -o wide > all-resources.txt
kubectl get nodes -o wide > nodes.txt
kubectl get pv,pvc -A > storage.txt
kubectl get svc -A > services.txt
kubectl get secrets -A > secrets-list.txt  # names only, not values
kubectl get configmaps -A > configmaps-list.txt
flux get all -A > flux-state.txt
flux get kustomizations > flux-kustomizations.txt
```

### 1.7 Synology iSCSI LUN inventory

The LUNs will be abandoned post-teardown (we're letting CSI provision fresh). Capture which exist so you can clean them up on Synology side after rebuild.

```fish
ssh ansible@10.0.21.21 sudo iscsiadm -m session  # Einherjar-urd
ssh ansible@10.0.21.22 sudo iscsiadm -m session  # Einherjar-verd
ssh ansible@10.0.21.23 sudo iscsiadm -m session  # Einherjar-skuld
```

Note all `iqn.2000-01.com.synology:munin.pvc-*` targets. Save the list.

### 1.8 PBS backup currency

In the PBS UI (`https://10.0.11.20:8007`) or SSH to LXC 1101:
- Confirm recent backups exist for VM IDs 2001, 2002, 2003, 2101, 2102, 2103
- Verify no failed jobs in last 24h
- Note backup snapshot IDs for the most recent good state (rollback reference)

### 1.9 Reachability baseline

```fish
ping -c 2 10.0.10.200    # AGH VIP
ping -c 2 10.0.11.20     # PBS
ping -c 2 10.0.11.220    # Factorio LXC
ping -c 2 10.0.254.20    # Synology
ping -c 2 10.0.254.11    # Urd
ping -c 2 10.0.254.12    # Verd
ping -c 2 10.0.254.13    # Skuld
```

All should succeed. During teardown only the 10.0.21.x range should become unreachable.

### 1.10 Sealed-secrets master keys backup ⚠️ CRITICAL

The sealed-secrets controller generates a fresh keypair on first start. Every SealedSecret in Git is encrypted against that pair's public cert. **If you skip this step, every existing SealedSecret becomes undecryptable after rebuild** and must be re-sealed from plaintext sources — adding 15-30 min of recovery work and requiring access to all the original plaintext values.

```fish
# Snapshot the active sealed-secrets master keypair
mkdir -p ~/homelab-rebuild-state
kubectl get secret \
  -n sealed-secrets \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
  -o yaml \
  > ~/homelab-rebuild-state/sealed-secrets-master-keys.yaml

# Verify it's not empty (~15 lines for a single active key)
wc -l ~/homelab-rebuild-state/sealed-secrets-master-keys.yaml
grep -c "tls.crt\|tls.key" ~/homelab-rebuild-state/sealed-secrets-master-keys.yaml
# Expected: 2 (one each)
```

**Then copy the file's contents into 1Password** as a Secure Note in the Homelab vault, named `sealed-secrets master keys — asgard <date>`. The local file alone isn't enough — if you lose the Mac mid-rebuild you've lost the keys.

This step is restored in Section 5.4 below, *before* the sealed-secrets controller starts on the rebuilt cluster.

---

## Section 2 — Per-tier rename (Git only, no infra changes)

Each tier is a self-contained commit. Verify each before moving on.

**Mac note:** all `perl -i -pe` commands work natively on macOS without the BSD-vs-GNU sed quoting issue.

### Tier A — Terraform module directories

```fish
cd ~/Dev/xiiisins/homelab

git mv terraform/proxmox/must-run-k3s terraform/proxmox/asgard-k3s
git mv terraform/proxmox/must-run-lxcs terraform/proxmox/asgard-lxcs

# Update text references inside the moved files
perl -i -pe 's/must-run/asgard/g; s/must_run/asgard/g' \
    terraform/proxmox/asgard-k3s/*.tf \
    terraform/proxmox/asgard-k3s/*.tfvars.example \
    terraform/proxmox/asgard-lxcs/*.tf \
    terraform/proxmox/asgard-lxcs/*.tfvars.example
```

Note: `terraform.tfvars` (without `.example`) is in `.gitignore` and isn't tracked, but if it has `must-run` strings, update it manually:

```fish
perl -i -pe 's/must-run/asgard/g; s/must_run/asgard/g' \
    terraform/proxmox/asgard-k3s/terraform.tfvars \
    terraform/proxmox/asgard-lxcs/terraform.tfvars
```

Review:

```fish
git diff
git status
```

Look for:
- Resource block names changed (e.g. `resource "..." "must_run_k3s_cp"` → `resource "..." "asgard_k3s_cp"`) — these are state-relevant for the LXCs module (NOT the K3s module which is being destroyed anyway).
- Variable names changed.
- Tags changed: `tags = ["must-run", ...]` → `tags = ["asgard", ...]`.

**Note on LXC state implications:** if the Factorio LXC resource block was named `must_run_lxc` or similar, the rename would normally cause Terraform to plan destroy+create. We don't want that. Two options:

1. Leave the *resource block name* unchanged (just rename the directory and update tags). This is the simplest.
2. Rename resource block too, then run `terraform state mv` to migrate state. Cleaner but riskier.

Recommend option 1 — check `terraform/proxmox/asgard-lxcs/lxcs.tf`:

```fish
grep -n 'resource\|module' terraform/proxmox/asgard-lxcs/lxcs.tf
```

If the resource block name doesn't contain `must-run` or `must_run`, you're already good. If it does, revert just that line with `git checkout`:

```fish
# Example, if the resource was renamed and you want to keep the old block name:
git diff terraform/proxmox/asgard-lxcs/lxcs.tf  # confirm what changed
# Manually edit lxcs.tf to keep resource block name as-is
```

Verify the LXC module still plans clean:

```fish
cd terraform/proxmox/asgard-lxcs
terraform plan
# Expected: only the tags = ["asgard", ...] change shows, nothing else
```

If that's the only plan diff, commit:

```fish
cd ~/Dev/xiiisins/homelab
git add -A
git commit -m "rename: terraform module dirs must-run-* → asgard-*

Rename of:
- terraform/proxmox/must-run-k3s/ → asgard-k3s/
- terraform/proxmox/must-run-lxcs/ → asgard-lxcs/

Text references within updated. Factorio LXC tags change from
\"must-run\" to \"asgard\" — will cause a no-op tag update on
next terraform apply. Resource block names preserved to keep
LXC state continuity."
```

### Tier B — Ansible

Files affected (from grep):
- `ansible/playbooks/must-run-k3s.yml` → `asgard-k3s.yml`
- `ansible/inventory/hosts.yml` — group names
- `ansible/roles/k3s/defaults/main.yml` — kubeconfig path
- `ansible/roles/k3s/handlers/main.yml` — group references
- `ansible/roles/k3s/tasks/install.yml` — group references
- `ansible/roles/k3s/templates/k3s.service.j2` — group references

```fish
cd ~/Dev/xiiisins/homelab

git mv ansible/playbooks/must-run-k3s.yml ansible/playbooks/asgard-k3s.yml

perl -i -pe 's/must-run/asgard/g; s/must_run/asgard/g; s/niflheim-must-run/niflheim-asgard/g' \
    ansible/playbooks/asgard-k3s.yml \
    ansible/inventory/hosts.yml \
    ansible/roles/k3s/defaults/main.yml \
    ansible/roles/k3s/handlers/main.yml \
    ansible/roles/k3s/tasks/install.yml \
    ansible/roles/k3s/templates/k3s.service.j2
```

Review:

```fish
git diff
```

Look for:
- `must_run_k3s` → `asgard_k3s` group names
- `must_run_k3s_cp` → `asgard_k3s_cp`
- `must_run_k3s_workers` → `asgard_k3s_workers`
- `must_run_lxcs` → `asgard_lxcs`
- Playbook `hosts: must_run_k3s` → `hosts: asgard_k3s`
- kubeconfig path updated

**Important — handle existing kubeconfig:**

```fish
# Move the existing kubeconfig to match new path (will be overwritten by playbook
# on rebuild, but useful during the rename phase if you need to kubectl anything)
mv ~/.kube/niflheim-must-run.yaml ~/.kube/niflheim-asgard.yaml
# Update your kubeconfig env if you use one
```

Verify Ansible parses:

```fish
cd ansible
ansible-inventory -i inventory/hosts.yml --list | head -20
ansible-playbook --syntax-check playbooks/asgard-k3s.yml
```

Both should succeed without errors.

Commit:

```fish
cd ~/Dev/xiiisins/homelab
git add -A
git commit -m "rename: Ansible groups/playbook/role refs must_run → asgard

Rename of:
- playbooks/must-run-k3s.yml → asgard-k3s.yml
- Inventory groups: must_run_k3s{,_cp,_workers}, must_run_lxcs → asgard_*
- k3s role handler/install task/service template group conditionals
- kubeconfig path: ~/.kube/niflheim-must-run.yaml → niflheim-asgard.yaml

Syntax-check + inventory parse verified."
```

### Tier C — Kubernetes/Flux

```fish
cd ~/Dev/xiiisins/homelab

git mv k8s/must-run k8s/asgard

# Update all text references inside k8s/asgard/
find k8s/asgard -type f \( -name '*.yaml' -o -name '*.yml' \) \
    -exec perl -i -pe 's/must-run/asgard/g' {} +
```

Review:

```fish
git diff
git status
```

Look for:
- `path: ./k8s/must-run/...` → `./k8s/asgard/...` in flux-system manifests
- Header comments updated
- `metallb-config.yaml` resource name `must-run` (pool name) → `asgard`
- The L2Advertisement reference to the pool name — needs to match

Verify the renamed pool name is consistent:

```fish
grep -n 'name:.*asgard\|name:.*must-run' k8s/asgard/metallb-config/metallb-config.yaml
# All should say asgard, none should say must-run
```

Render kustomization to confirm it parses:

```fish
kustomize build k8s/asgard/infrastructure > /tmp/asgard-infrastructure.yaml
kustomize build k8s/asgard/infrastructure-config > /tmp/asgard-config.yaml
kustomize build k8s/asgard/metallb-config > /tmp/asgard-metallb.yaml
echo "All builds succeeded"
```

Commit:

```fish
cd ~/Dev/xiiisins/homelab
git add -A
git commit -m "rename: k8s/must-run → k8s/asgard

Rename of:
- k8s/must-run/ → k8s/asgard/
- All Flux Kustomization spec.path references updated
- MetalLB IPAddressPool name 'must-run' → 'asgard'
  (L2Advertisement reference updated to match)
- Header path comments in manifests updated

kustomize build verified for all three Kustomization roots."
```

### Tier D — Documentation

Outline dir gets deleted (it's a draft, regenerable from `homelab-design.md` later). Sweep is then just `CLAUDE.md` + `homelab-design.md`.

```fish
cd ~/Dev/xiiisins/homelab

# Delete the outline drafts — predate D1, regenerable from homelab-design.md later
git rm -r docs/outline/

# Content sweep on the two live docs
perl -i -pe '
    s/must-run/asgard/g;
    s/Must-run/Asgard/g;
    s/can-run/jotunheim/g;
    s/Can-run/Jotunheim/g;
    s/HL-CORE/HL-ASG/g;
    s/HL-CR\b/HL-JOT/g;
    s/niflheim-must-run/niflheim-asgard/g;
' CLAUDE.md docs/homelab-design.md
```

Note the `\b` word boundary on `HL-CR` (defensive — no current collisions, but safe).

Review:

```fish
git diff CLAUDE.md docs/homelab-design.md | less
git status  # should show docs/outline/ deletions + 2 modified files
```

Commit:

```fish
git add -A
git commit -m "rename: docs must-run/can-run → asgard/jotunheim, drop outline drafts

Content sweep across CLAUDE.md and docs/homelab-design.md:
- must-run/Must-run → asgard/Asgard
- can-run/Can-run → jotunheim/Jotunheim
- HL-CORE-* → HL-ASG-*
- HL-CR-* → HL-JOT-*
- niflheim-must-run kubeconfig refs → niflheim-asgard

Also: deleted docs/outline/ entirely. These 22 files were initial
drafts generated from the original homelab-design.md and never kept
in sync. They predate D1 and contain stale framing (Authentik
incorrectly in jotunheim tier, two-layer secrets architecture, etc.).
Regenerable from current homelab-design.md when an outline doc set
is actually wanted."
```

### Tier E — UCG-Ultra VLAN names (manual UI step)

In the UniFi UI at `https://unifi.ui.com` or directly on UCG-Ultra:
1. Settings → Networks
2. For each VLAN, rename:
   - VLAN 10 `HL-CORE-VIP` → `HL-ASG-VIP`
   - VLAN 11 `HL-CORE-SVC` → `HL-ASG-SVC`
   - VLAN 20 `HL-CORE-K3S-VIP` → `HL-ASG-K3S-VIP`
   - VLAN 21 `HL-CORE-K3S-WRK` → `HL-ASG-K3S-WRK`
   - VLAN 30 `HL-CR-K3S-VIP` → `HL-JOT-K3S-VIP`
   - VLAN 31 `HL-CR-K3S-WRK` → `HL-JOT-K3S-WRK`

VLAN IDs and subnets unchanged — purely cosmetic UI labels. No network impact.

Verify firewall rules still reference these by ID, not by name (UCG uses IDs internally).

No commit (not in IaC).

---

## Section 3 — Pre-teardown final checks

After all rename commits, before destroying anything:

```fish
cd ~/Dev/xiiisins/homelab

# Confirm no stale references
git grep -nE 'must.run|must_run|can.run|HL-CORE|HL-CR' \
    | grep -v 'docs/teardown-rebuild.md'  # this file itself
# Expected: empty (or only this runbook's own contents)
```

If anything remains, fix it before proceeding.

```fish
# Confirm Terraform module still plans clean for LXCs
cd terraform/proxmox/asgard-lxcs
terraform plan
# Expected: 1 change (tag update on Factorio LXC). Apply it now if you want:
terraform apply
```

The K3s module (`asgard-k3s/`) will show a plan that destroys+creates everything because of the directory rename + resource name changes. That's expected and is what we're about to do anyway.

---

## Section 4 — Teardown

**Point of no return below.** Confirm state capture (Section 1) is complete, root token and recovery keys are in 1Password and verified working, KV data is captured to `~/homelab-rebuild-state/`.

### 4.1 Drain workloads (graceful)

```fish
# Optional but cleaner — stops Vault writes mid-destroy
kubectl scale statefulset vault -n vault --replicas=0
sleep 30
```

### 4.2 Destroy K3s VMs

```fish
cd ~/Dev/xiiisins/homelab/terraform/proxmox/asgard-k3s
terraform destroy
# Confirm "yes" when prompted. Destroys VMs 2001-2003 and 2101-2103.
```

This takes 5–10 minutes. Watch for errors — bpg/proxmox sometimes leaves stale agent locks. If destroy hangs on a VM, force-remove via Proxmox UI.

### 4.3 Clean local kubeconfig

```fish
rm ~/.kube/niflheim-asgard.yaml  # will be re-fetched by playbook
# If you have it added to KUBECONFIG env, the next kubectl will fail until rebuild
```

### 4.4 Clean Synology stale LUNs

The LUNs that backed the destroyed K3s PVs are now orphaned (retain policy). Find them via DSM:
- DSM → SAN Manager → LUN
- Look for LUNs with names matching `pvc-<uuid>` (Synology CSI pattern)
- Cross-reference with the LUN list captured in Section 1.7
- Delete each one

Also clear stale iSCSI sessions on the Proxmox hosts (defensive — though these should be on the now-destroyed VMs, not the hosts):

```fish
# Not strictly needed for hosts since VMs are gone, but if you reboot a host
# later and it still has stale session records (it shouldn't), this is the fix:
# ssh root@10.0.254.11 iscsiadm -m node -o delete
```

### 4.5 Pre-rebuild verification

```fish
ping -c 2 10.0.21.11  # gondul — expected: no route / dest unreachable
ping -c 2 10.0.10.200 # AGH VIP — expected: success (unaffected)
ping -c 2 10.0.11.220 # Factorio — expected: success (unaffected)
```

---

## Section 5 — Rebuild

### 5.1 Recreate K3s VMs (Terraform)

```fish
cd ~/Dev/xiiisins/homelab/terraform/proxmox/asgard-k3s
terraform apply
# Confirm. Takes 5–10 minutes to create 6 VMs.
```

Verify VMs come up (cloud-init can take 2–3 min after Terraform reports done):

```fish
for ip in 10.0.21.11 10.0.21.12 10.0.21.13 10.0.21.21 10.0.21.22 10.0.21.23
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$ip 'hostname' 2>&1
end
```

Note: SSH host keys will be different (fresh VMs). Clear them from `~/.ssh/known_hosts`:

```fish
for ip in 10.0.21.11 10.0.21.12 10.0.21.13 10.0.21.21 10.0.21.22 10.0.21.23
    ssh-keygen -R $ip
end
```

### 5.2 Bootstrap with Ansible

Day-1 bootstrap (baseline-only, as root):

```fish
cd ~/Dev/xiiisins/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml \
    -e 'ansible_user=root' --tags baseline
```

Full deploy (as `ansible` user — hardening locks root out at the end):

```fish
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml
```

Takes 15–30 minutes. K3s installs in this run; Calico addon manifest is dropped on init node and applied by addon controller.

### 5.3 Verify K3s + Calico

The kubeconfig is fetched to `~/.kube/niflheim-asgard.yaml` by the play.

```fish
set -x KUBECONFIG ~/.kube/niflheim-asgard.yaml

kubectl get nodes -o wide
# Expected: 3 CP + 3 worker, all Ready

kubectl get pods -n calico-system
# Expected: tigera-operator + calico-node (3 instances) + calico-kube-controllers running

kubectl get installation default -o jsonpath='{.spec.calicoNetwork}{"\n"}'
# Expected: nodeAddressAutodetectionV4 cidrs=[10.0.21.0/24], mtu=1450,
# encapsulation VXLANCrossSubnet, ipPools 10.42.0.0/16
```

### 5.4 Bootstrap Flux

The Flux bootstrap re-creates the GitRepository source and points it at the new path:

```fish
flux bootstrap github \
    --owner=<your-github-user> \
    --repository=homelab \
    --branch=main \
    --path=./k8s/asgard/flux-system \
    --personal \
    --private \
    --kubeconfig ~/.kube/niflheim-asgard.yaml
```

Or, if you have the bootstrap manifests already committed and want to re-apply directly:

```fish
kubectl apply -k k8s/asgard/flux-system/flux-system
# Then Flux self-reconciles from Git
```

**Notes on `flux bootstrap github`:**
- The bootstrap re-creates the GitHub deploy key idempotently if it still exists in the repo settings; otherwise generates a new keypair. The deploy key itself is NOT in IaC (tracked as a future hardening item).
- The bootstrap Secret containing the deploy key lives only in the cluster — not backed up. Re-bootstrapping is the recovery path.

#### 5.4.1 Restore sealed-secrets master keypair ⚠️ CRITICAL

**This step must happen between Flux bootstrap and the sealed-secrets controller's first reconcile.** If the controller starts before the keypair Secret exists, it generates a *new* keypair and all SealedSecrets in Git become undecryptable.

```fish
# Apply the backed-up master keypair (from Section 1.10)
kubectl apply -f ~/homelab-rebuild-state/sealed-secrets-master-keys.yaml

# Verify
kubectl get secret -n sealed-secrets \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active
# Expected: at least one Secret listed
```

If you skipped Section 1.10 — recovery is to re-seal each SealedSecret from plaintext:
```fish
# Get the new public cert from the controller
kubeseal --fetch-cert --controller-namespace=sealed-secrets > /tmp/pub-cert.pem

# Then for each SealedSecret in Git, re-seal from its plaintext source:
# vault-unseal: plaintext in Ansible Vault (group_vars/all/vault.yml — aws_access_key_id,
#   aws_secret_access_key, aws_kms_key_id, AWS_REGION=eu-west-1)
# synology-csi: plaintext in 1Password (Synology kubernetes user creds)
echo -n "<value>" | kubectl create secret generic <name> -n <ns> \
  --dry-run=client --from-file=<key>=/dev/stdin -o yaml \
  | kubeseal --cert /tmp/pub-cert.pem -o yaml > k8s/asgard/<component>-config/<name>.yaml
# Commit, push, reconcile.
```

Watch Flux reconciliation:

```fish
flux get all -A --watch
```

Expected order:
1. `flux-system` Kustomization comes up
2. `infrastructure` Kustomization reconciles — installs sealed-secrets, synology-csi, vault, external-secrets, metallb, tigera-operator (operator already installed by Ansible, this is a no-op)
3. `infrastructure-config` reconciles after `infrastructure` ready — installs ClusterSecretStore
4. `metallb-config` reconciles after `infrastructure` ready — installs IPAddressPool + L2Advertisement
5. `vault-config` reconciles after `infrastructure` ready — applies vault-unseal SealedSecret
6. `synology-csi-config` reconciles after `infrastructure` ready — applies synology-csi SealedSecret

**Gotcha — SealedSecret CRD timing:** if you ever add a NEW SealedSecret directly under `infrastructure/`, Flux dry-run will fail with `no matches for kind "SealedSecret" in version "bitnami.com/v1alpha1"` because the CRD doesn't exist at dry-run time. Always put SealedSecrets in a `<component>-config/` Kustomization that `dependsOn: infrastructure`.

### 5.5 Vault — fresh init, capture keys, restore data

Vault comes up via the HelmRelease but is **uninitialized** (new iSCSI LUN, no prior data).

```fish
# Wait for pods to be running but Sealed
kubectl get pods -n vault -w
# Once vault-0 is Running 0/1 (sealed):

kubectl exec -n vault vault-0 -- vault operator init \
    -recovery-shares=5 \
    -recovery-threshold=3 \
    -format=json > ~/homelab-rebuild-state/vault-init.json
```

**Gotcha — stuck init state.** If `vault operator init` exits non-zero (e.g. you ran it before all pods were ready), the Raft data dir can end up half-initialized. Subsequent `init` attempts fail with `stored unseal keys are supported, but none were found in the storage backend`. Recovery:

```fish
kubectl delete statefulset vault -n vault --cascade=orphan
kubectl delete pvc -n vault data-vault-0 data-vault-1 data-vault-2
kubectl delete pod -n vault vault-0 vault-1 vault-2 --force --grace-period=0
flux reconcile helmrelease vault -n vault --force
# Wait for pods to come back Running 0/1, then retry init
# Note: old iSCSI LUNs on Synology survive (retain policy) — clean up via DSM after success
```

**CRITICAL:** the init output contains the new root token + 5 recovery key shares. Update 1Password:
- Replace the old root token entry
- Replace the old recovery keys entry
- Tag with rebuild date

```fish
# Vault should now auto-unseal via AWS KMS
kubectl exec -n vault vault-0 -- vault status
# Expected: Sealed: false, Initialized: true
```

**Gotcha — Raft followers don't auto-join.** vault-1 and vault-2 *should* auto-discover vault-0 and join via the chart's `retry_join` config, but in practice they often miss the join window if they started before vault-0 was initialized. Symptom: they sit Sealed with `stored unseal keys are supported, but none were found.` in their logs.

Manual join:

```fish
kubectl exec -n vault vault-1 -- vault operator raft join http://vault-0.vault-internal:8200
kubectl exec -n vault vault-2 -- vault operator raft join http://vault-0.vault-internal:8200

# They auto-unseal via KMS after joining (~30 seconds)
# Verify
kubectl exec -n vault vault-0 -- vault operator raft list-peers
# Expected: 3 members, all voter, all not-leader except one
```

### 5.6 Vault — re-apply Terraform config

```fish
cd ~/Dev/xiiisins/homelab/terraform/vault

# Set new root token
set -x VAULT_ADDR http://10.0.20.11:8200  # MetalLB VIP for vault-ui service
set -x VAULT_TOKEN <new-root-from-step-5.5>

terraform plan
# Expected: lots of additions (KV engine, K8s auth + config, AppRole + roles, eso policy + role)
terraform apply
```

### 5.7 Vault — restore KV data

```fish
# Re-write the entries you captured in Section 1.4
vault kv put secret/ansible/sftpgo/admin-password \
    value=(jq -r '.data.data.value' ~/homelab-rebuild-state/sftpgo-admin-password.json)

# Verify
vault kv get secret/ansible/sftpgo/admin-password
```

### 5.8 Vault — re-generate AppRole SecretIDs

The AppRole roles exist (Terraform created them) but no SecretIDs are issued yet.

```fish
# ansible-local — for your MacBook
vault read -format=json auth/approle/role/ansible-local/role-id \
    | jq -r '.data.role_id'
# Save this RoleID — it's also available as terraform output
terraform -chdir=~/Dev/xiiisins/homelab/terraform/vault output ansible_local_role_id

# Generate a fresh SecretID
vault write -f -format=json auth/approle/role/ansible-local/secret-id \
    | jq -r '.data.secret_id'
```

Update `~/.config/ansible/vault-approle.env` with the new RoleID and SecretID:

```fish
echo "set -x ANSIBLE_HASHI_VAULT_AUTH_METHOD approle
set -x ANSIBLE_HASHI_VAULT_URL http://10.0.20.11:8200
set -x ANSIBLE_HASHI_VAULT_ROLE_ID <new-role-id>
set -x ANSIBLE_HASHI_VAULT_SECRET_ID <new-secret-id>" > ~/.config/ansible/vault-approle.env
chmod 600 ~/.config/ansible/vault-approle.env
```

Update 1Password recovery copy of the AppRole credentials.

Test the lookup:

```fish
ansible-vault-env  # source the env vars
cd ~/Dev/xiiisins/homelab/ansible
ansible-playbook playbooks/test-vault-lookup.yml
# Expected: pulls a value from secret/ansible/...
```

### 5.9 ESO ClusterSecretStore — verify Ready

```fish
kubectl get clustersecretstore vault -o yaml | grep -A5 status:
# Expected: conditions: type Ready, status True
```

### 5.10 MetalLB — verify VIP reachable from outside the cluster

```fish
kubectl get svc -A | grep LoadBalancer
# Look for vault-ui service — should have an EXTERNAL-IP from the pool (10.0.20.11 by default)

# Local ping (loopback path through the cluster)
ping -c 2 10.0.20.11

# External HTTP (real test — exercises route_localnet + VLAN 20 policy routing)
curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://10.0.20.11:8200/v1/sys/health
# Expected: 200 (active) or 429 (rate-limited but TCP path works) — anything that returns proves the full chain
```

The `roles/k3s/tasks/network.yml` role applies four things needed for VIPs to be reachable from outside the cluster on multi-homed workers:
- Calico autodetection pin (`cidrs: ["10.0.21.0/24"]`)
- `rp_filter=2` (loose mode)
- `route_localnet=1`
- `vlan20-policy-routing.service` systemd unit

If `curl` fails but `ping` succeeds, one of the four is missing. Verify with:
```fish
ssh ansible@10.0.21.21 'sysctl net.ipv4.conf.all.rp_filter net.ipv4.conf.all.route_localnet'
# Expected: rp_filter=2, route_localnet=1
ssh ansible@10.0.21.21 'sudo systemctl is-active vlan20-policy-routing.service'
# Expected: active
ssh ansible@10.0.21.21 'sudo ip rule list | grep 10.0.20'
# Expected: from 10.0.20.0/24 lookup vlan20
```

---

## Section 6 — Verification checklist

After rebuild, walk through:

- [ ] `kubectl get nodes` shows 3+3 Ready
- [ ] `kubectl get pods -A` shows nothing in CrashLoopBackOff or Error
- [ ] `flux get all -A` shows all Kustomizations Ready, all HelmReleases Released
- [ ] `kubectl get clustersecretstore vault -o jsonpath='{.status.conditions[0].status}'` returns `True`
- [ ] `vault status` shows Initialized + Unsealed on all 3 pods
- [ ] `vault operator raft list-peers` shows 3 members
- [ ] MetalLB VIP 10.0.20.11 reachable via HTTP from outside the cluster (proves full plumbing chain: route_localnet + policy routing + L2 advertisement)
- [ ] AppRole lookup from local Ansible works (`ansible-playbook playbooks/test-vault-lookup.yml`)
- [ ] Sealed-secrets master keypair matches the pre-rebuild backup (`kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml | diff - ~/homelab-rebuild-state/sealed-secrets-master-keys.yaml`)
- [ ] Factorio LXC still reachable on TCP 22022 (SFTP) and UDP 34197 (game) — verified externally if possible
- [ ] AdGuard still resolving DNS for the network
- [ ] PBS LXC still backing up — wait for next scheduled job or trigger manually
- [ ] Reachability baseline (Section 1.9) — all IPs except 10.0.20.11/10.0.21.x originally are unchanged
- [ ] AdGuard DNS records for new VMs (Göndul, Hlökk, Sigrún, Einherjar-*) point at correct IPs — re-add if cleared
- [ ] Idempotency check: `ansible-playbook playbooks/asgard-k3s.yml` returns `changed=0` across all 6 hosts (proves `detect-state.yml` is working — install/calico skipped)

Update `docs/homelab-design.md` build status section:

- "Asgard K3s" entries flip from ✅ to "✅ (rebuilt YYYY-MM-DD)"
- Update "Status (date)" lines

Commit any docs corrections discovered during verification.

---

## Section 7 — Rollback

If the rebuild gets stuck and you can't recover within your time budget:

### 7.1 PBS restore

The pre-rebuild PBS snapshots (captured in Section 1.8) contain the last-known-good VMs.

In Proxmox UI:
1. Datacenter → Storage → pbs
2. Find backups for VM IDs 2001–2003 + 2101–2103
3. For each: Restore → choose target node + storage

Note: PBS restores create VMs with the snapshot's original IDs. If `terraform apply` already created new VMs at those IDs, you'll need to first `terraform destroy` what came up partially, OR restore to new IDs and adjust.

### 7.2 Revert Git rename

If you want to back out the rename entirely:

```fish
cd ~/Dev/xiiisins/homelab
git log --oneline -10  # find the commit before the rename started
git reset --hard <pre-rename-commit-sha>
```

Then run terraform from the now-restored `terraform/proxmox/must-run-k3s/` against the PBS-restored VMs.

### 7.3 What you do NOT roll back

- The Vault re-init's new recovery keys + root token (1Password is updated; reverting Git doesn't undo this)
- Synology iSCSI LUNs deleted in Section 4.4 are gone — but they only held K3s PV data, all of which was either re-created (configmap-style data) or restored from capture (Vault KV)

---

## Appendix A — what to do if you spot an "I forgot to put X in IaC" moment

The whole point of this rebuild is to discover gaps. When you hit one:

1. **Stop, document.** Don't manually fix — that's how IaC gaps perpetuate. Write down what's missing.
2. **If non-blocking:** finish the rebuild, add a `terraform import` or backfill task to D-list, address after.
3. **If blocking:** manually fix to unblock, but commit a follow-up task to capture the manual fix in IaC.

Likely gap candidates to watch for:
- AdGuard custom DNS records (currently manual config, not in IaC)
- Anything you configured imperatively via `kubectl` or `vault write` between D1 completion and rebuild
- Cloudflare DNS records (not yet in IaC)
- AWS KMS key + IAM user config (not yet in IaC — G2 backlog item)

---

## Appendix B — Partial rebuild: replacing a single CP

This is a different procedure from the full-cluster rebuild above. Use it when you want to destroy and recreate ONE control plane VM (e.g. relocating gondul to a different host, or replacing failed hardware) while the other CPs and workers keep running.

**Scenario it handles:** the surviving cluster has 2/3 CP quorum; you're swapping out the third.

**Risk:** while the CP is being rebuilt, etcd is at 2/3 — a second failure during the window means quorum loss. Don't do this if another node is already unhealthy.

### B.1 Pre-conditions

```fish
# Verify quorum and the OTHER two CPs are healthy
kubectl get nodes
kubectl exec -n vault vault-0 -- vault operator raft list-peers

# Optional: snapshot the surviving CPs as safety net
ssh root@10.0.254.12 "qm snapshot 2002 before-cp-rebuild"
ssh root@10.0.254.13 "qm snapshot 2003 before-cp-rebuild"
```

### B.2 Edit Terraform

Change whatever needed changing (`target_node`, `cores`, `memory`) for the target CP in `locals.control_planes`. Apply with `--target` so you only touch one VM:

```fish
cd ~/Dev/xiiisins/homelab/terraform/proxmox/asgard-k3s
terraform plan
# Expected: 1 to add, 1 to destroy (or "1 to replace")
terraform apply --target='proxmox_virtual_environment_vm.control_plane["<name>"]'
```

After apply, the new VM is up but has no K3s.

### B.3 Remove the stale etcd member ⚠️ CRITICAL

The cluster still considers the old VM an etcd member. New VM tries to join with the same name → "duplicate node name found" → systemd restart loop.

```fish
kubectl delete node <name>
# K3s's node-delete handler also evicts the stale etcd member
```

If the new VM's k3s.service is already in a systemd restart loop (because the playbook already ran and failed), the next retry will succeed automatically once the node entry is removed.

### B.4 Run the playbook

```fish
# Clean stale SSH host key
ssh-keygen -R <ip>

# Run the playbook. Override k3s_init_node IF the CP you're rebuilding IS the default init node (gondul).
ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'

# If you're NOT rebuilding the default init node, no override needed:
ansible-playbook playbooks/asgard-k3s.yml --limit <name>
```

Without the override when rebuilding the default init node, the role would `--cluster-init` the new VM as a fresh cluster, ignoring the existing 2-CP cluster. The override tells the role "this CP is joining, not initing."

### B.5 Verify rejoin

```fish
kubectl get nodes
# All 6 Ready

kubectl exec -n vault vault-0 -- vault operator raft list-peers
# 3 members again

# Idempotency check
ansible-playbook playbooks/asgard-k3s.yml --limit <name>
# Expected: changed=0
```

### B.6 Cleanup

```fish
# Drop the safety snapshots once stable for >1 hour
ssh root@10.0.254.12 "qm delsnapshot 2002 before-cp-rebuild"
ssh root@10.0.254.13 "qm delsnapshot 2003 before-cp-rebuild"
```

**Why this is appendix-worthy:** every step here is non-obvious. The `kubectl delete node` requirement, the `-e k3s_init_node=` override, and the difference between "VM rebuild via terraform" and "K3s rejoin via ansible" each cost an hour of debugging on first encounter. This appendix bakes the recovery in.
