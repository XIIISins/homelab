<!-- docs/procedures/agh-cutover.md -->

# AdGuard Home cutover (manual → IaC, Phase 5b.2)

One-shot deployment runbook to lift the manually-installed AGH trio (Saga / Mimir / Kvasir) into Terraform + Ansible management. After this completes, the LXCs are TF-defined + the AGH/sync/keepalived config is Ansible-rendered + the secrets live in Vault.

**Estimated downtime:** seconds-per-failover during the rolling apply (each LXC reboot triggers a VIP failover; clients re-resolve in under a minute). Plan for ~10 minutes total wall-clock if everything goes smoothly, ~30 minutes if you hit a snag.

**Risk:** DNS is the most-load-bearing service in the homelab. If the cutover bricks the trio entirely, K3s pods will flap and the homelab's resolvers become whatever each client has configured as a fallback. **Always have at least one healthy node serving DNS before touching the next.**

---

## Prerequisites

### 1. Code committed + pushed

```
git log --oneline --grep '5b.2' | head -5
```

You should see at least `feat(5b.2): AdGuard Home IaC — local code prep` (commit `3345ff0` or later).

### 2. Vault paths populated

Three secrets must exist before the first Ansible run. Mint fresh values (do **NOT** reuse anything from the manual install — both leaked secrets were exposed in operator transcripts during the read-only fetch):

```bash
# Source homelab env first (sets VAULT_ADDR + VAULT_TOKEN)
. ~/dev/xiiisins/homelab/.config/scripts/homelab.sh
homelab-env
set-vault-token root

# 1. AdGuard admin password (the bcrypt hash that goes into AdGuardHome.yaml)
NEW_ADMIN_PW=$(pwgen -s 24 1)
NEW_ADMIN_HASH=$(htpasswd -bnBC 10 "" "$NEW_ADMIN_PW" | tr -d ':\n' | sed 's/^//')
vault kv put secret/ansible/adguard/admin-password-hash hash="$NEW_ADMIN_HASH"

# 2. Same plaintext, this time for adguardhome-sync's HTTP basic-auth
vault kv put secret/ansible/adguardhome-sync/admin-password password="$NEW_ADMIN_PW"

# 3. VRRP auth_pass (keepalived limits this to 8 chars)
NEW_VRRP_AUTH=$(pwgen -s 8 1)
vault kv put secret/ansible/keepalived/adguard_vrrp auth_pass="$NEW_VRRP_AUTH"

# 4. Stash the plaintext admin password in 1P for human lookup
echo "$NEW_ADMIN_PW" | op item create --vault Homelab \
  --category Login \
  --title "Asgard - AdGuard - admin login" \
  username=admin password=-
unset NEW_ADMIN_PW NEW_ADMIN_HASH NEW_VRRP_AUTH
```

Verify:

```bash
vault kv get secret/ansible/adguard/admin-password-hash | grep hash
vault kv get secret/ansible/adguardhome-sync/admin-password | grep password
vault kv get secret/ansible/keepalived/adguard_vrrp | grep auth_pass
```

### 3. Decide username

The current manual deploy uses `ghost` as the admin user. The IaC default is `admin`. Either:

- **Keep `ghost`**: add `adguard_admin_user: ghost` to `ansible/inventory/group_vars/adguard_hosts.yml`. The bcrypt hash above gets associated with `ghost` instead of `admin`.
- **Migrate to `admin`**: leave defaults. The 1P item should also say `admin`.

Pick one; consistency matters more than the value.

### 4. Discover current VMIDs

The IaC will create LXCs at `vmid = 1110/1111/1112`. If the manual install already occupies those IDs, `terraform apply` will refuse. Need to know:

```bash
# Run on each Proxmox host
ssh root@10.0.254.11 'qm list; pct list' | grep -E "11(10|11|12)|saga|mimir|kvasir"
ssh root@10.0.254.12 'qm list; pct list' | grep -E "11(10|11|12)|saga|mimir|kvasir"
ssh root@10.0.254.13 'qm list; pct list' | grep -E "11(10|11|12)|saga|mimir|kvasir"
```

Record which physical host each AGH LXC currently lives on + its VMID.

### 5. Verify health of all 3 AGH nodes before starting

```bash
# Should answer with HTTP 200 (web UI)
for ip in 10.0.11.201 10.0.11.202 10.0.11.203; do
  curl -sk -o /dev/null -w "%{http_code} $ip\n" "http://$ip/"
done
# Should answer with the cluster's wildcard rewrites
dig +short @10.0.10.200 netbox.niflheim.xiiisins.com
```

If any node is unhealthy BEFORE the cutover, fix that first — don't compound problems mid-cutover.

---

## Cutover strategy decision

Based on the prerequisite-4 findings:

### Strategy A — terraform import (current VMIDs match 1110/1111/1112)

Lower-risk if the manual install already has the IDs we want. The LXCs stay running throughout; only the config inside them changes (via Ansible).

```bash
cd terraform/proxmox/asgard-lxcs
terraform import 'proxmox_virtual_environment_container.adguard["saga"]' urd/1110
terraform import 'proxmox_virtual_environment_container.adguard["mimir"]' verd/1111
terraform import 'proxmox_virtual_environment_container.adguard["kvasir"]' skuld/1112
terraform plan      # expect: tag changes, description change, possibly hostname normalization
terraform apply
```

After the import-apply, jump to Ansible-side ([§ "Ansible rollout"](#ansible-rollout)).

### Strategy B — destroy + recreate (current VMIDs differ from 1110/1111/1112)

Higher-risk but cleaner — fresh LXCs match the IaC spec exactly. Requires rolling: keep ≥1 node serving DNS at all times.

```bash
# 1. Verify VIP holder. The current MASTER is what we destroy LAST.
ssh root@10.0.11.201 'ip -br addr show eth1' | grep 10.0.10.200
# If 10.0.10.200 is bound on Saga → Saga last. (Normal — priority 100.)

# 2. Destroy Kvasir first (lowest VRRP priority — has no clients glued to it).
ssh root@10.0.254.13 'pct stop <current-kvasir-vmid> && pct destroy <current-kvasir-vmid>'

# 3. Bring up Kvasir via TF.
cd terraform/proxmox/asgard-lxcs
terraform apply -target='proxmox_virtual_environment_container.adguard["kvasir"]'

# 4. Provision Kvasir via Ansible (see § "Ansible rollout" but just for kvasir).
ansible-playbook ansible/playbooks/adguard-host.yml -l kvasir

# 5. Verify Kvasir is in the cluster (joined VRRP as BACKUP at priority 80, syncing from Saga).
dig +short @10.0.11.203 netbox.niflheim.xiiisins.com
# Should match what Saga returns.

# 6. Repeat for Mimir.
ssh root@10.0.254.12 'pct stop <current-mimir-vmid> && pct destroy <current-mimir-vmid>'
terraform apply -target='proxmox_virtual_environment_container.adguard["mimir"]'
ansible-playbook ansible/playbooks/adguard-host.yml -l mimir
dig +short @10.0.11.202 netbox.niflheim.xiiisins.com

# 7. Saga LAST. Before destroying, drain VRRP from it so the VIP moves cleanly.
ssh root@10.0.11.201 'systemctl stop keepalived'
# Wait ~3 seconds for VIP to land on Mimir (priority 90).
ssh root@10.0.11.202 'ip -br addr show eth1' | grep 10.0.10.200  # should show VIP now
ssh root@10.0.254.11 'pct stop <current-saga-vmid> && pct destroy <current-saga-vmid>'

# 8. Bring up Saga via TF.
terraform apply -target='proxmox_virtual_environment_container.adguard["saga"]'

# 9. Provision Saga via Ansible (this also installs adguardhome-sync, since saga is the primary).
ansible-playbook ansible/playbooks/adguard-host.yml -l saga

# 10. Wait for sync to populate Saga's config from... wait, Saga is the origin. So sync FROM saga TO mimir+kvasir.
# The new Saga needs the operator-managed rewrites + clients + filters BEFORE sync runs.
# Restore from PBS snapshot of /opt/AdGuardHome/AdGuardHome.yaml or recreate via UI.
```

⚠️ **Strategy B requires restoring Saga's operator-managed AdGuardHome.yaml state (rewrites, clients, filters) from a backup** — the Ansible role only renders the base config. PBS snapshots the LXC; the latest snapshot's `/opt/AdGuardHome/AdGuardHome.yaml` is your friend.

---

## Ansible rollout

Whether you took Strategy A or B, the per-LXC Ansible run is the same. Bootstrap as `root` first (sets up the `ansible` user), then re-run as `ansible`:

```bash
# Day 1: bootstrap the ansible user
ansible-playbook ansible/playbooks/adguard-host.yml \
  -e 'ansible_user=root' \
  --tags baseline

# Day N: full play as the ansible user (default)
ansible-playbook ansible/playbooks/adguard-host.yml
```

The role-level tags let you re-run a single concern without the full play:

```bash
# Just re-render AdGuardHome.yaml + restart
ansible-playbook ansible/playbooks/adguard-host.yml --tags adguard:config

# Just re-do keepalived (e.g. after VRRP auth rotation)
ansible-playbook ansible/playbooks/adguard-host.yml --tags keepalived
```

---

## NetBox-side update

After the LXCs are alive at vmids 1110/1111/1112, fill in the NetBox view:

```bash
cd terraform/netbox
terraform apply
# Expect: 3 in-place updates on saga/Mimir/kvasir VMs (the vmid="" → "1110|1111|1112"
# update from the carry-forward commit 3345ff0).
```

---

## Verification

After all three nodes are up + provisioned:

```bash
# 1. All three nodes answer DNS.
for ip in 10.0.11.201 10.0.11.202 10.0.11.203 10.0.10.200; do
  dig +short @$ip netbox.niflheim.xiiisins.com
done
# Expect all four to return 10.0.20.10 (the netbox HTTPRoute target).

# 2. VIP failover works.
# Holder before failover:
ssh ansible@saga 'ip -br addr show eth1 | grep 10.0.10.200'
ssh ansible@saga 'systemctl stop keepalived'
sleep 3
ssh ansible@mimir 'ip -br addr show eth1 | grep 10.0.10.200'   # should show VIP now
ssh ansible@saga 'systemctl start keepalived'
sleep 5
ssh ansible@saga 'ip -br addr show eth1 | grep 10.0.10.200'    # should be back on saga

# 3. Sync drift check (after at least one cron tick has fired).
for ip in 10.0.11.201 10.0.11.202 10.0.11.203; do
  echo "=== $ip rewrites ==="
  curl -s -u admin:$(op item get 'Asgard - AdGuard - admin login' --fields password) \
    "http://$ip/control/rewrite/list" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d), "rewrites")'
done
# All three should report the same count. Sub-microsecond "Sync done" log entries
# are NOT trustworthy — compare counts directly (CLAUDE.md gotcha).

# 4. systemd-resolved is masked + AdGuardHome.service is active on all three.
for h in saga mimir kvasir; do
  ssh ansible@$h 'systemctl is-active AdGuardHome.service; systemctl is-active systemd-resolved'
done
# Expect: active / inactive

# 5. NetBox shows the three with correct VMIDs + sizing + IPs.
curl -sk -H "Authorization: Token $NETBOX_API_TOKEN" \
  "$NETBOX_SERVER_URL/api/virtualization/virtual-machines/?role=dns" | python3 -m json.tool | head
```

---

## Rollback plan

If anything goes catastrophically wrong:

1. **VIP unreachable**: SSH to whichever node should be VRRP master, check `systemctl status keepalived` + `journalctl -u keepalived -n 50`. The most common cause is VRRP auth mismatch — confirm `secret/ansible/keepalived/adguard_vrrp` has the same `auth_pass` across all three runs (it does if you only minted it once).
2. **DNS broken cluster-wide**: temporarily point every K3s node + LXC + workstation at `1.1.1.1` (operator already has this as bootstrap fallback). Gives breathing room.
3. **Worst case — restore from PBS**: each AGH LXC was snapshotted before the cutover (or should have been; verify in PBS UI before starting). `pct restore <vmid> <snapshot>` rolls back. Then re-run the manual-install bootstrap.
4. **Code-level rollback**: `git revert <5b.2 commit-sha>` to back out the IaC; the manual install state was the previous reality.

---

## Post-flight

After verification passes:

1. **Update `docs/operations/build-sequence.md`** — mark 5b.2 row ✅ Closed with the actual cutover date.
2. **Update `docs/operations/open-questions.md`** — close the AGH-related items:
   - `AdGuard Home IaC pending`
   - `AGH sync interval */30 → */1` (now done via role default)
   - `Document adguardhome-sync location and config in the AdGuard role` (now in role README)
3. **Update `docs/services/adguard.md`** — drop the "Phase 5b.2 IaC pending" qualifier in the title, update the "Status" line.
4. **CLAUDE.md "Current build status"** — flip 🟡 AdGuard Home → ✅, drop the "manually installed, no Ansible role" caveat.
5. **Write incident retro** if anything non-trivial surfaced — `docs/incidents/YYYY-MM-DD-5b2-agh-cutover.md`.
6. **Commit + push** the post-flight docs.

## See also

- [`docs/services/adguard.md`](../services/adguard.md)
- [`ansible/roles/adguard/README.md`](../../ansible/roles/adguard/README.md)
- [`ansible/roles/adguardhome-sync/README.md`](../../ansible/roles/adguardhome-sync/README.md)
- Phase 5g.2 cutover patterns (HAProxy / keepalived VIP — same VRRP failover semantics) — see [`docs/incidents/2026-05-24-5g2-haproxy-keepalived-vip.md`](../incidents/2026-05-24-5g2-haproxy-keepalived-vip.md)
- CLAUDE.md "Known gotchas → DNS" + "HAProxy / keepalived"
