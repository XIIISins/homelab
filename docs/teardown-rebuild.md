# Teardown & rebuild — must-run K3s → asgard

*Last updated: 2026-05-16*

The first deliberate end-to-end rebuild of the production K3s cluster. Combines a directory/naming rename (`must-run` → `asgard`, `can-run` → `jotunheim`) with a clean teardown and rebuild from IaC. Validates that the IaC is complete and the rebuild path actually works.

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
- Outline docs content refresh — mechanically renamed only; framing-sync is a separate backlog item

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

Renames + content sweep. Docs are the largest single tier by line count.

```fish
cd ~/Dev/xiiisins/homelab

git mv docs/outline/06-must-run.md docs/outline/06-asgard.md
git mv docs/outline/19-services-must-run.md docs/outline/19-services-asgard.md
git mv docs/outline/20-services-can-run.md docs/outline/20-services-jotunheim.md

# Content sweep — all docs and CLAUDE.md
perl -i -pe '
    s/must-run/asgard/g;
    s/Must-run/Asgard/g;
    s/can-run/jotunheim/g;
    s/Can-run/Jotunheim/g;
    s/HL-CORE/HL-ASG/g;
    s/HL-CR\b/HL-JOT/g;
    s/niflheim-must-run/niflheim-asgard/g;
' CLAUDE.md docs/homelab-design.md docs/outline/*.md
```

Note the `\b` word boundary on `HL-CR` to avoid clobbering anything else that starts with `HL-CR` (none currently, but defensive).

Review:

```fish
git diff CLAUDE.md docs/homelab-design.md | less
git diff docs/outline/ | less
```

**Manual checks:**

1. `docs/outline/01-overview.md` has a hyperlink: `[Services — must-run](./19-services-must-run.md)`. After the sweep, the link text becomes `[Services — asgard]` and the URL becomes `./19-services-asgard.md`. The URL also needs the rename, which the perl sweep handles.

```fish
grep -n 'services-must-run\|services-can-run' docs/outline/*.md
# Should return nothing — all hyperlinks updated
```

2. **Stale framing alert:** `docs/outline/*.md` files were written before the D1 architectural changes. After mechanical rename, content like "Asgard tier (boring, stable)" and "Jotunheim — learning environment" still misrepresents the current framing. **Don't fix this in this commit.** Add a backlog item:

> Backlog: bring `docs/outline/*.md` in sync with `docs/homelab-design.md` post-D1.
> - `01-overview.md` framing of tiers
> - `11-identity-authentik.md` incorrectly places Authentik in jotunheim
> - `13-secrets-architecture.md` pre-D1 two-layer framing
> - `14-secrets-bootstrap.md` pre-D1 Ansible-Vault-as-legacy framing

Commit:

```fish
cd ~/Dev/xiiisins/homelab
git add -A
git commit -m "rename: docs must-run/can-run → asgard/jotunheim

Mechanical rename across CLAUDE.md, docs/homelab-design.md, and
docs/outline/*.md:
- must-run/Must-run → asgard/Asgard
- can-run/Can-run → jotunheim/Jotunheim
- HL-CORE-* → HL-ASG-*
- HL-CR-* → HL-JOT-*
- niflheim-must-run kubeconfig refs → niflheim-asgard
- 06-must-run.md, 19-services-must-run.md, 20-services-can-run.md
  renamed accordingly; intra-doc hyperlinks updated.

Note: docs/outline/*.md framing predates D1 and is not refreshed in
this commit. Added as a separate backlog item."
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

Watch reconciliation:

```fish
flux get all -A --watch
```

Expected order:
1. `flux-system` Kustomization comes up
2. `infrastructure` Kustomization reconciles — installs sealed-secrets, synology-csi, vault, external-secrets, metallb, tigera-operator (operator already installed by Ansible, this is a no-op)
3. `infrastructure-config` reconciles after `infrastructure` ready — installs ClusterSecretStore
4. `metallb-config` reconciles after `infrastructure` ready — installs IPAddressPool + L2Advertisement

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

**CRITICAL:** the output contains the new root token + 5 recovery key shares. Update 1Password:
- Replace the old root token entry
- Replace the old recovery keys entry
- Tag with rebuild date

```fish
# Vault should now auto-unseal via AWS KMS
kubectl exec -n vault vault-0 -- vault status
# Expected: Sealed: false, Initialized: true
```

Other Vault pods (vault-1, vault-2) join the Raft cluster automatically and auto-unseal. Wait ~60 seconds and verify:

```fish
kubectl exec -n vault vault-0 -- vault operator raft list-peers
# Expected: 3 members
```

### 5.6 Vault — re-apply Terraform config

```fish
cd ~/Dev/xiiisins/homelab/terraform/vault

# Set new root token
set -x VAULT_ADDR http://10.0.20.11:8200  # MetalLB VIP for vault-ui service
set -x VAULT_TOKEN <new-root-from-step-5.5>

# If terraform/vault/main.tf still has `import {}` blocks from the original
# D1 work, remove them now — the resources don't exist yet, so import will fail.
grep -n 'import {' main.tf
# If any matches, edit them out (or comment with #) before applying.

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

### 5.10 MetalLB — verify VIP reachable

```fish
kubectl get svc -A | grep LoadBalancer
# Look for vault-ui service — should have an EXTERNAL-IP from the pool (10.0.20.11 by default)

ping -c 2 10.0.20.11
# Expected: success
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
- [ ] MetalLB VIP 10.0.20.11 reachable, Vault UI loads on `:8200`
- [ ] AppRole lookup from local Ansible works (test-vault-lookup playbook)
- [ ] Factorio LXC still reachable on TCP 22022 (SFTP) and UDP 34197 (game) — verified externally if possible
- [ ] AdGuard still resolving DNS for the network
- [ ] PBS LXC still backing up — wait for next scheduled job or trigger manually
- [ ] Reachability baseline (Section 1.9) — all IPs except 10.0.20.11/10.0.21.x originally are unchanged
- [ ] AdGuard DNS records for new VMs (Göndul, Hlökk, Sigrún, Einherjar-*) point at correct IPs — re-add if cleared

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

## Appendix A — outline docs framing drift (backlog)

After the mechanical rename, `docs/outline/*.md` still has pre-D1 framing in several places. **Not blocking the rebuild**, tracked separately:

- `01-overview.md` — "Asgard (boring, stable) and jotunheim (learning environment)" misrepresents current framing. Both clusters host real services; difference is failure-domain risk.
- `06-asgard.md` — likely has old service list; check against current `homelab-design.md` Must-run K3s section.
- `07-k3s.md` — describes jotunheim cluster as "learning environment, complexity intentional" — old framing.
- `11-identity-authentik.md` — line 43 places Authentik in jotunheim. WRONG — Authentik is asgard (core infrastructure, cascade-failure criterion).
- `13-secrets-architecture.md` — pre-D1 two-layer framing. Should describe three-store model.
- `14-secrets-bootstrap.md` — describes Ansible Vault as "only used during bootstrap" and "legacy after". New framing: Ansible Vault is the permanent bootstrap layer.
- `19-services-asgard.md` — service list likely out of date (Tofu Controller, AWX promotion, etc.).
- `20-services-jotunheim.md` — service list shrunk (SMTP dropped, several promoted to asgard).
- `21-build-sequence.md` — may not reflect current build status.

Add to the backlog list in `docs/homelab-design.md`:

```
- [ ] Refresh `docs/outline/*.md` to match post-D1 framing
  in `docs/homelab-design.md`. Mechanical rename done; content
  sync is the remaining piece.
```

## Appendix B — what to do if you spot an "I forgot to put X in IaC" moment

The whole point of this rebuild is to discover gaps. When you hit one:

1. **Stop, document.** Don't manually fix — that's how IaC gaps perpetuate. Write down what's missing.
2. **If non-blocking:** finish the rebuild, add a `terraform import` or backfill task to D-list, address after.
3. **If blocking:** manually fix to unblock, but commit a follow-up task to capture the manual fix in IaC.

Likely gap candidates to watch for:
- AdGuard custom DNS records (currently manual config, not in IaC)
- Anything you configured imperatively via `kubectl` or `vault write` between D1 completion and rebuild
- Cloudflare DNS records (not yet in IaC)
- AWS KMS key + IAM user config (not yet in IaC — G2 backlog item)
