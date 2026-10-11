<!-- docs/outline/procedures/teardown-rebuild.md -->

# Teardown & rebuild

The disaster-recovery path: rebuild the homelab from the Proxmox layer up using only the repository and the bootstrap secrets. The full runbook is also mirrored as a PDF in 1Password and cloud storage so it is reachable when the homelab, Munin and this wiki are all down. It contains pointers, never secrets.

**Use when:** the cluster, a tier of LXCs or the whole homelab must be rebuilt. **Operator-run, owner-approved, two working days.**
**Do not use for** a single node (use **K3s node rebuild**) or a single database (use **Postgres operations**).

> **Be honest about validation.** The asgard K3s path (§3) is validated end to end (a full teardown and rebuild, plus single control-plane and worker swaps). The foundation, LXC-tier and data-restore sections (§1, §4 to §6) are derived from live IaC and have **not** been exercised as one cold rebuild. Expect gaps; follow Appendix B when you hit one.

---

## Entry point: if everything is down

The recovery chain has four legs, none of which depend on the homelab:

1. **The repository.** `git clone git@github.com:XIIISins/homelab.git`. The complete spec: Terraform, Ansible, Flux.
2. **The bootstrap secrets in 1Password**, vault **"Homelab 2.0"**. To rebuild from zero you need:
   - `[Bootstrap] - Manual - Vault - Root token` and `… - Recovery keys`: Vault root and unseal.
   - `[Bootstrap] - Manual - AWS - KMS unseal access key` and `… - Vault - KMS unseal config`: auto-unseal (region `eu-west-1` and the key ARN; the ARN embeds the AWS account id, kept in 1Password, not Git).
   - `[Bootstrap] - Manual - Sealed Secrets - Master keys`: decrypts every SealedSecret; **apply it before the sealed-secrets controller starts** (§3.4).
   - `[Bootstrap] - Manual - Flux - Deploy key`: reuse it so `flux bootstrap` does not orphan the GitHub deploy key.
   - `[Asgard] - Ansible - Vault - AppRole (ansible-local)` (username = RoleID, password = SecretID) and `[Asgard] - Manual - Ansible - Vault password` (decrypts `group_vars/all/vault.yml`).
   - `[Infra] - ansible - SSH - Private key` and `[Infra] - recovery - SSH - Private key`.
   - `[Infra] - Terraform - Proxmox - API token` (and `Root password` for ticket-auth LXCs); `[Asgard] - Terraform - AWS - State access key` (and the bootstrap key for the `aws/` module).
   - `[Infra] - Manual - Synology - Admin login`, `… - Synology - kubernetes user`, `… - UCG-Ultra - Admin login`. (KPN has no 1Password item; its configuration is in the network docs.)
   - `[Asgard] - Manual - K3s - Kubeconfig (asgard)`.
3. **This runbook.**
4. **A control node**: any machine with `op`, `terraform`, `ansible`, `kubectl`, `vault`, `flux`, `kubeseal`, `jq`.

**Reference the offline mirror, not memory.** Fetch credentials dynamically (`op read …`, `vault kv get …`). Never type a literal secret into a shell.

## Scope and order

Assumed standing: Proxmox on Urd/Verd/Skuld, the `niflheim` cluster formed, VM/LXC templates present. Rebuilt here: everything else. Anything not in IaC (UCG-Ultra, KPN, Synology base config) is rebuilt by hand in §1 from the architecture docs.

```
§0 Control node + secrets      §1 Foundation (manual): network, Synology
§2 Terraform state             §3 Asgard K3s core: VMs -> K3s -> Flux -> sealed-secrets key -> Vault -> Vault TF -> infrastructure
§4 AdGuard + Tailscale LXCs    §5 Data tier: etcd -> Patroni -> restore data
§6 App + monitoring LXCs       §7 Secret-minting Terraform + Flux apps
§8 Fleet agents + Semaphore    §9 Verification
```

**Day-1 bring-up uses targeted `--limit` plays, never `site.yml`** (it is the steady-state tool and refuses a small inventory). One Ansible playbook at a time.

| § | Section | Time | Day |
|---|---|---|---|
| 0 | Control node and secrets | 20 to 30 min | 1 |
| 1 | Network (UCG, KPN) and Synology | 90 to 180 min | 1 |
| 2 | Terraform state | 10 min | 1 |
| 3 | Asgard K3s core (3.1 to 3.7) | 2 to 3 h | 1 |
| 4 | AdGuard + Tailscale | 30 to 45 min | 2 |
| 5 | Data tier | 45 to 75 min | 2 |
| 6 | App and monitoring LXCs | 60 to 90 min | 2 |
| 7 | Secret-minting Terraform + apps | 30 to 60 min | 2 |
| 8, 9 | Agents, Semaphore, verification | 45 min | 2 |

**Clean pause point: end of §3.** Do not pause inside §3.2 (Ansible mid-play), §3.5 (Vault init) or §5 (Patroni bootstrap); finish the section or revert it.

---

## §0 Control node and secrets

```bash
git clone git@github.com:XIIISins/homelab.git ~/homelab && cd ~/homelab
op vault list | grep -i 'Homelab 2.0'                          # 1Password CLI signed in
# Shell tooling (bash/zsh): load homelab-env
. .config/scripts/homelab.sh
homelab-env                                                    # loads VAULT_ADDR, ANSIBLE_HASHI_VAULT_*, AWS_*, ... from 1Password; cached 24 h

ansible-galaxy collection install -r ansible/requirements.yml
pipx inject ansible hvac                                       # if Ansible is pipx-installed
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES                 # macOS only

# SSH keys for node access (ansible + recovery break-glass)
install -m 600 /dev/null ~/.ssh/homelab_ansible  && op read 'op://Homelab 2.0/e5mkz32pdzde7ouxq62w3hajx4/private key' > ~/.ssh/homelab_ansible
install -m 600 /dev/null ~/.ssh/homelab_recovery && op read 'op://Homelab 2.0/zekpfaskgxceks32yqitamzzqe/private key' > ~/.ssh/homelab_recovery
# Ansible Vault password file
mkdir -p ~/.config/ansible && install -m 600 /dev/null ~/.config/ansible/vault-pass
op read 'op://Homelab 2.0/wte7tqcuwh5b32ljrh4jnmbcpy/password' > ~/.config/ansible/vault-pass
```

`VAULT_TOKEN` is not loaded yet (Vault is down); it arrives in §3.5. The rest of this page assumes this shell with `homelab-env` loaded (and `KUBECONFIG=~/.kube/niflheim-asgard.yaml` after §3.2).

---

## §1 Foundation (manual, not in IaC)

### 1.1 Network: UCG-Ultra and KPN (30 to 60 min)

Restore from the network design. Recreate in the UniFi UI, **exactly** (everything downstream is hard-coded to these):

| VLAN | Subnet | Name |
|---|---|---|
| 1 | `10.0.254.0/24` | HL-MGMT |
| 10 | `10.0.10.0/24` | HL-ASG-VIP |
| 11 | `10.0.11.0/24` | HL-ASG-SVC |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-NODE |
| 30 / 31 | `10.0.30.0/24`, `10.0.31.0/24` | reserved (HL-JOT-…) |
| 60 | `10.0.60.0/24` | HL-CLIENT |
| 100 | `10.0.100.0/24` | HL-STOR |
| 222 | `10.0.222.0/24` | Untrusted |

- **Zone firewall:** all VLANs in the Internal zone; `Internal → Any: Allow`; `External → Internal: Allow Return`; `Any → Any: Deny` last. Confirm the Deny rule exists **before** enabling the KPN DMZ.
- **Port-forwards (UCG only):** Teamspeak voice UDP 9987 and file transfer TCP 30033 → `10.0.20.12`; Factorio UDP 34197 → `10.0.11.220`. Everything HTTP goes through the Cloudflare tunnel (cloudflared dials out; no inbound rule).
- **KPN Experia Box:** "exposed host"/DMZ forwarding all unsolicited inbound (IPv4 **and** IPv6 tabs) to the UCG WAN IP. KPN keeps outbound NAT for `192.168.2.0/24` only.
- The AIOps egress allow-lists (Gná, Ratatoskr) are manual policies; recreate from the network design.

### 1.2 Synology (Munin) (60 to 120 min)

DSM is manual UI. Recreate:

- **Volumes** on the RAID-1 pool: Volume1 (backups: PBS datastore, Hyper Backup), Volume2 (all K8s storage: the `k8s-nfs` share and the one iSCSI LUN class), Volume5 (media).
- **Shared folders and NFS exports:** `proxmox-backup` (PBS datastore), `k8s-nfs` (every `nfs-client` PVC), `media` (read/write for the three workers `10.0.21.21-23`, read-only for Jellyfin `10.0.11.223`, Squash "Map all users to admin"), and the rest of the share inventory in the Synology doc. Match the export paths (`/volumeN/…`); a stale path means stale NFS handles on clients.
- **iSCSI:** install SAN Manager. Synology CSI creates one target and LUN per PVC at provision time; nothing to pre-create. **The DSM-wide LUN ceiling is about 10**; the tiered storage layout keeps you far below it.
- **`kubernetes` user** (admin) for the CSI driver; credentials in 1Password (carried into the cluster by a SealedSecret).
- **Tailscale** (out-of-band access): Appendix C.

**Reboot-test Munin** after volume, share or export changes before depending on it (boot-mount failures hide until the next reboot).

---

## §2 Terraform state sanity (10 min)

State lives in S3 (`xiiisins-homelab-tfstate`, eu-west-1, native lock): off-homelab, so it survives a full wipe. Each module will refresh, find resources gone, and recreate them.

```bash
# The aws/ module (state bucket + IAM) uses the BOOTSTRAP identity
set-aws-creds bootstrap
cd terraform/aws && terraform init && terraform plan        # expect no changes (bucket and IAM survived)
cd ../..
set-aws-creds state                                          # runtime identity for every other module (or `homelab-env`)
```

If the S3 bucket itself is gone (the whole AWS account lost), that is a different DR: every other module's state is gone too, and every `apply` becomes a from-scratch create; a Vault snapshot restore is then the only way back to KV data. Note it and proceed; modules are written to create from empty.

Apply discipline: `terraform apply` only from the main checkout. Re-check `VAULT_TOKEN` before each `vault_*` module apply on a long day: it expires mid-flight.

---

## §3 Asgard K3s core

### 3.1 K3s VMs (10 min)

```bash
cd terraform/proxmox/asgard-k3s && terraform init && terraform apply     # VMs 2001-2003 (CP), 2101-2103 (workers)
for ip in 10.0.21.11 10.0.21.12 10.0.21.13 10.0.21.21 10.0.21.22 10.0.21.23; do
  ssh-keygen -R $ip; ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$ip hostname
done
```

Cloud-init takes 2 to 3 minutes after apply returns. Each worker has a 50 GB `scsi1` data disk (`/data`, the `local-path` tier).

### 3.2 K3s bootstrap with Ansible (25 to 35 min)

> **Cold-boot DNS break.** The node default resolvers are the AdGuard VIP and the UCG, both dead until AdGuard comes up in §4, yet the K3s and Calico install needs DNS to pull images. Override to a temporary resolver for this run only, then re-converge in §4.

```bash
cd ~/homelab/ansible
# Day-1 baseline as root (hardening later locks root SSH), temporary resolvers
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml -e ansible_user=root \
  -e '{"baseline_nameservers":["1.1.1.1","10.0.254.1"]}' --tags baseline
# Full deploy as the ansible user. Init node = gondul by default
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml -e '{"baseline_nameservers":["1.1.1.1","10.0.254.1"]}'

export KUBECONFIG=~/.kube/niflheim-asgard.yaml                 # fetched by the role
kubectl get nodes -o wide                                       # 3 CP + 3 worker Ready
kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}{"\n"}'   # cidrs=[10.0.21.0/24], NOT firstFound
```

`detect-state.yml` gates install on health, so re-runs are safe.

### 3.3 Flux bootstrap and deploy key (10 min)

Restore the deploy-key Secret first so bootstrap reuses it:

```bash
kubectl create namespace flux-system
op read 'op://Homelab 2.0/v4swe4y3i55oawjlsc3rcatmvu/notesPlain' | kubectl apply -f -      # [Bootstrap] - Manual - Flux - Deploy key
flux bootstrap github --owner=XIIISins --repository=homelab --branch=main \
  --path=./k8s/asgard/flux-system --personal --private --kubeconfig ~/.kube/niflheim-asgard.yaml
```

### 3.4 Sealed-secrets master keypair restore ⚠️ CRITICAL (5 min)

**Do this between Flux bootstrap and the sealed-secrets controller's first reconcile.** If the controller starts first it creates a *new* keypair and every SealedSecret in Git (`vault-unseal`, `synology-csi`, …) becomes undecryptable.

```bash
op read 'op://Homelab 2.0/mv4u6dyzjkzx6aogvxbmxz3vcm/notesPlain' | kubectl apply -f -      # [Bootstrap] - Manual - Sealed Secrets - Master keys
kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active   # expect >=1
```

If you raced the controller: re-seal each SealedSecret from plaintext against the controller's new certificate (`kubeseal --fetch-cert`; `vault-unseal` plaintext is in `group_vars/all/vault.yml`, `synology-csi` credentials in 1Password), commit, push, reconcile.

### 3.5 Vault recovery (20 to 45 min)

Vault comes up from its HelmRelease **uninitialised** (fresh `local-path` volume). KMS settings come from the `vault-unseal` SealedSecret restored in §3.4. HA is Raft quorum; PBS backs up each worker's `/data`.

**Path A: Raft snapshot restore (primary; preserves KV, auth, policies, AppRole roles).** Use the newest snapshot (the daily one in S3 `vault-raft/`, or a hand-taken one).

```bash
kubectl -n vault get pods -w                               # wait for vault-0 Running (sealed, 0/1)
# Initialise a fresh barrier so the pod unseals (KMS), then restore the old data over it
kubectl -n vault exec vault-0 -c vault -- vault operator init -recovery-shares=5 -recovery-threshold=3 -format=json > /tmp/vault-init-tmp.json
kubectl -n vault exec vault-0 -c vault -- vault status | grep -E "Initialized|Sealed"       # true / false
kubectl -n vault cp <latest>.snap vault/vault-0:/tmp/vault.snap -c vault
kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" \
  vault operator raft snapshot restore -force /tmp/vault.snap
kubectl -n vault exec vault-0 -c vault -- rm /tmp/vault.snap
shred -u /tmp/vault-init-tmp.json 2>/dev/null || rm -f /tmp/vault-init-tmp.json
```

After the restore: KV, policies, auth methods (kubernetes and approle) and both AppRole *roles* are back. AppRole *SecretIDs* are not in a snapshot: re-mint them in §3.6. Cloudflare records, Authentik objects, the Tailscale ACL and NetBox rows are external state and return when their Terraform modules are applied in §7. The restore reverts recovery keys and the root token to the snapshot's (the 1Password bootstrap values).

**Path B: fresh init (fallback, no usable snapshot).** Rotates everything and ripples through every consumer; prefer Path A.

```bash
kubectl -n vault exec vault-0 -c vault -- vault operator init -recovery-shares=5 -recovery-threshold=3 -format=json > /tmp/vault-init.json
# Put the new root token and the five recovery keys into 1Password (replace the bootstrap entries, tag with the rebuild date), then delete the file.
```

§3.6 re-applies `terraform/vault/` to recreate auth, policies and roles, and every secret-minting module in §7 re-mints its KV entries; operator-minted KV (Discord webhooks, SAML keypairs) is restored by hand from the 1Password mirror (`scripts/secrets/vault-1p-mirror to-vault`).

**Followers (both paths).** `vault-1` and `vault-2` often miss the auto-join window. Join them (TLS, pass the CA); KMS auto-unseals in about 30 seconds:

```bash
for n in 1 2; do kubectl -n vault exec vault-$n -c vault -- vault operator raft join -leader-ca-cert=@/vault/userconfig/vault-tls/ca.crt https://vault-0.vault-internal:8200; done
kubectl -n vault exec vault-0 -c vault -- env VAULT_TOKEN="$(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password')" vault operator raft list-peers   # 3 members, all voter
```

If init hit a stuck partial state, see **Vault recovery** §6.

### 3.6 Vault Terraform config and AppRole SecretIDs (15 min)

```bash
set-vault-token root && homelab-env --refresh               # mint VAULT_TOKEN from 1Password, persist it
vault token lookup >/dev/null && echo ok
cd terraform/vault && terraform init && terraform plan
# Path A: near no-op (config came back with the snapshot). Path B: large additions.
terraform apply
cd ../..

# SecretIDs are never in Terraform state or the snapshot: re-mint
vault write -f auth/approle/role/ansible-local/secret-id        # paste secret_id + accessor into 1Password item 4srpqv2mt2vditxo7g5rqjquti (NOT username)
homelab-env --refresh && seed-vault-approle
set-vault-token approle                                          # prove the AppRole logs in
unset VAULT_TOKEN
# ansible-awx (Semaphore) is re-minted with rotate-semaphore-approle after Semaphore returns (§7)
```

### 3.7 Infrastructure and storage tiers (20 to 40 min)

Flux drives the rest of `infrastructure/`. Watch it converge:

```bash
flux get all -A --watch          # Ctrl-C when stable
```

Order: `infrastructure` (sealed-secrets, ESO, MetalLB, synology-csi, csi-driver-nfs, local-path-provisioner, Traefik, Gateway API, cert-manager, cloudflared, Garage, observability) → the `*-config` Kustomizations (`infrastructure-config`, `metallb-config`, `vault-config`, `synology-csi-config`, `cert-manager-config`, `gateway-config`, `vpa-config`).

```bash
kubectl get clustersecretstore vault -o jsonpath='{.status.conditions[0].status}{"\n"}'     # True
kubectl get sc                                                  # local-path, nfs-client, synology-csi-iscsi-retain-vol2 (no default class by design)
kubectl get pods -n metallb-system,traefik,cert-manager,garage,monitoring
curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://10.0.20.10/                    # any HTTP code = L2 + policy routing work (VIPs do not answer ping)
kubectl get pv | grep vault                                     # Vault PVs Bound on local-path
```

The `local-path` tier needs each worker's `scsi1` `/data` disk mounted with the SELinux `context=` option (the `local-path-disk` role).

> **Day-1 pause point.** Cluster up, Vault restored with 3 voters, infrastructure healthy. Safe to stop.

---

## §4 AdGuard and Tailscale LXCs (30 to 45 min)

These need Vault (their roles read `secret/ansible/adguard/*` and `secret/ansible/tailscale/*`).

```bash
# AdGuard (Saga/Mimir/Kvasir)
terraform -chdir=terraform/proxmox/asgard-lxcs apply        # creates all asgard LXCs
cd ~/homelab/ansible && set-vault-token approle
ansible-playbook -i inventory/hosts.yml playbooks/asgard-adguard.yml

# Tailscale (Bifrost/Heimdall/Gjallarbru): device passthrough needs the root-ticket module
cd ~/homelab && PROXMOX_VE_PASSWORD="$(op read 'op://Homelab 2.0/6vv32uzlahikgmkvkiqfnkgshy/password')" terraform -chdir=terraform/proxmox/asgard-lxcs-root apply
cd ansible && ansible-playbook -i inventory/hosts.yml playbooks/asgard-tailscale.yml
```

**Tailscale auth keys cap at 90 days.** If the last `terraform/tailscale` apply was more than 90 days ago, apply it (§7) **before** the Ansible run, or `tailscale up` fails with an expired key.

**Re-converge node DNS to AdGuard** (drop the temporary resolver):

```bash
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --tags baseline          # no -e override
kubectl rollout restart deploy -n kube-system coredns
dig +short @10.0.10.200 pbs.niflheim.xiiisins.com                                           # resolves after terraform/adguard (§7)
```

---

## §5 Data tier: etcd, then Patroni, then data (45 to 75 min)

> On a cold boot **etcd must exist before Patroni**: Patroni's DCS is etcd. (`site.yml` lists postgres first, which is only right in steady state.)

### 5.1 HAProxy and etcd trio

```bash
ansible-playbook -i inventory/hosts.yml playbooks/asgard-haproxy-etcd.yml
for ip in 10.0.11.233 10.0.11.234 10.0.11.235; do curl -s http://$ip:2379/health; echo; done      # all {"health":"true"}
```

**One playbook at a time**: concurrent runs race on the `restart etcd` handler and lose quorum. This play has no `serial:`; do not force handlers.

### 5.2 PostgreSQL and Patroni

```bash
ansible-playbook -i inventory/hosts.yml playbooks/asgard-postgres.yml
ssh -o IdentitiesOnly=true -i "$ANSIBLE_PRIVATE_KEY_FILE" ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'     # one Leader, two Replicas streaming
```

### 5.3 Restore database data

A fresh Patroni cluster is **empty**. The app databases (authentik, netbox, zabbix, teamspeak3, outline, semaphore, immich) must be restored from the per-node daily logical dumps in `/var/backups/postgresql` (`globals.sql` + `<db>.dump`, leader-written, captured by PBS). Recover the dump files with a PBS file-restore of the newest Postgres-leader backup (**Backups & restore drills** §4b). Then, on the current Patroni leader, **globals first**:

```bash
LEADER=<leader-ip>; SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
$SSH ansible@$LEADER 'sudo -iu postgres psql -f /var/backups/postgresql/globals.sql'
for db in authentik netbox zabbix teamspeak3 outline semaphore immich; do
  $SSH ansible@$LEADER "sudo -iu postgres createdb $db && sudo -iu postgres pg_restore -d $db /var/backups/postgresql/$db.dump"
done
```

App roles and passwords come from Vault: run the `postgres-common` provisioning against the **current leader** (**Postgres operations** §6); `--limit fulla` is unsafe because leadership floats. The alternative is restoring the three Postgres LXCs wholesale from PBS (faster to last-known-good, skips the IaC validation, and the etcd state must still be consistent). There is no WAL archiving or PITR: effective history is one day.

---

## §6 Application and monitoring LXCs (60 to 90 min)

All depend on Vault and most on the Postgres VIP. Any order within the section.

```bash
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/asgard-pbs.yml        # build PBS FIRST if it is also gone: it is the restore source (§6.1)
ansible-playbook -i inventory/hosts.yml playbooks/asgard-factorio.yml   # LXC 1120, SFTPGo + reconcile loop
ansible-playbook -i inventory/hosts.yml playbooks/asgard-hermod.yml     # LXC 1103, AppriseAPI + Caddy
ansible-playbook -i inventory/hosts.yml playbooks/asgard-zabbix.yml     # LXC 1102, Zabbix 7.0
ansible-playbook -i inventory/hosts.yml playbooks/asgard-gna.yml        # LXC 1121, n8n + Toolbelt client
ansible-playbook -i inventory/hosts.yml playbooks/asgard-ratatoskr.yml  # LXC 1122, Discord bot
ansible-playbook -i inventory/hosts.yml playbooks/asgard-jellyfin.yml   # LXC 1123 (created by the -root module in §4)
```

Zabbix: the factory login `Admin`/`zabbix` is rotated to the Vault value through the UI **after** first login (do not pre-log-in with the Vault value; five failures lock the account: clear it with `UPDATE users SET attempt_failed=0, attempt_clock=0, attempt_ip='' WHERE username='Admin';` on the leader). Frigg (HA VM 2900): `terraform/proxmox/asgard-vms` and `asgard-control.yml`. The AIOps pieces need their Vault entries and Discord/Anthropic credentials. Media pods return with the Flux apps in §7.

### 6.1 PBS (LXC 1101 on Urd, never on Skuld)

PBS is the restore source for §5.3. If it is also gone, rebuild it right after §1.2. The datastore is the `proxmox-backup` NFS export on Munin, mounted inside the LXC.

```bash
ansible-playbook -i inventory/hosts.yml playbooks/asgard-pbs.yml
ssh root@pbs.niflheim.xiiisins.com 'systemctl restart proxmox-backup proxmox-backup-proxy; proxmox-backup-manager datastore list'
# Restore-verify with a real file restore before relying on it
```

---

## §7 Secret-minting Terraform and Flux apps (30 to 60 min)

These recreate external-provider resources and write their machine secrets to Vault. On Path A the KV writes reconcile; on Path B they are the only source of those entries. From the main checkout, in dependency order:

```bash
terraform -chdir=terraform/cloudflare apply              # tunnel, DNS, cloudflared credentials
terraform -chdir=terraform/authentik apply               # OIDC/SAML providers, users and groups
terraform -chdir=terraform/tailscale apply               # ACL, auth keys, split DNS
terraform -chdir=terraform/netbox apply -parallelism=2   # ~160 records; the standing rule for this module
kubectl -n garage port-forward svc/garage-admin 3903:3903 &      # Garage admin is ClusterIP only
terraform -chdir=terraform/garage apply                  # bucket, key, grant, secret/k8s/outline/s3
kill %1
terraform -chdir=terraform/semaphore apply               # pushes ansible-awx SecretID + app config
terraform -chdir=terraform/adguard apply                 # DNS rewrites (to Saga; sync fans out)
terraform -chdir=terraform/proxmox/zabbix-access apply   # PVE token for Zabbix
```

Path B also: re-mint `ansible-awx` (`rotate-semaphore-approle`, fish) and restore operator-minted KV from the mirror (`scripts/secrets/vault-1p-mirror to-vault`).

The Flux apps (`k8s/asgard/apps/`) converge once their ExternalSecrets resolve:

```bash
flux reconcile kustomization apps --with-source
flux get hr -A                                           # all Released
kubectl get externalsecrets -A | grep -v SecretSynced
kubectl annotate -n <ns> externalsecret <name> force-sync=$(date +%s) --overwrite     # for any stale after a fresh KV write
```

Apps with Postgres backends pick up the restored databases through the VIP. VictoriaMetrics and VictoriaLogs are refillable by design: no restore step.

---

## §8 Fleet agents and Semaphore (15 min)

```bash
cd ~/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/vlagent.yml
ansible-playbook -i inventory/hosts.yml playbooks/zabbix-agent.yml
ansible-playbook -i inventory/hosts.yml playbooks/zabbix-host-groups.yml
```

Semaphore (a K3s app from §7) takes over steady state: `refresh-netbox-inventory` (4 h), `asgard-drift-check` (6 h), `asgard-apply` (manual), `asgard-fleet-agents` (daily). The drift-check baseline is **zero `changed=` across the fleet**; once green, a Hermod `alert` means real drift.

---

## §9 Verification checklist (30 min)

**Foundation**
- [ ] All VLANs route; the `Any → Any Deny` rule confirmed before the DMZ flip
- [ ] Munin shares and exports mount from a client; SAN Manager up; the `kubernetes` user works
- [ ] PBS datastore mounted; a test file-restore succeeds

**Asgard K3s**
- [ ] `kubectl get nodes` → 3 + 3 Ready; `kubectl get pods -A` shows no CrashLoopBackOff
- [ ] `flux get all -A` → Kustomizations Ready, HelmReleases Released
- [ ] `clustersecretstore vault` is `True`; `vault status` unsealed on all three; `raft list-peers` shows 3 voters
- [ ] The sealed-secrets active keypair matches the 1Password backup
- [ ] Traefik `10.0.20.10` answers HTTP from outside the cluster
- [ ] AppRole lookup works; `asgard-k3s.yml` converges `changed=0`

**LXC tiers**
- [ ] AdGuard VIP `10.0.10.200` resolves (TCP test, not ping); failover works
- [ ] Tailscale LXCs and Munin online, advertising routes
- [ ] etcd 3 members; Patroni one leader + two streaming replicas; VIP `10.0.10.210` routes to the leader
- [ ] App databases restored and apps connected (Authentik login, NetBox, Outline, Semaphore, Zabbix, Teamspeak)
- [ ] Factorio reachable; Hermod end to end; Zabbix collecting from agents

**Apps and external**
- [ ] External hostnames resolve through cloudflared (`authentik.`, `wiki.xiiisins.com`, …)
- [ ] Internal `*.niflheim` and `*.midgard` hostnames resolve through AdGuard to Traefik
- [ ] The Semaphore drift check returns `changed=0` across the fleet

Then update `docs/operations/build-sequence.md` and file any IaC gaps you found.

---

## §10 Rollback

If the rebuild stalls past budget, restore last-known-good from **PBS**: Proxmox UI → Datacenter → Storage → `pbs-backup` → the backup for the guest → Restore, or `pct restore <ctid> pbs-backup:backup/ct/<ctid>/<ts> --storage local-lvm --force`. PBS restores keep the original ID: if `terraform apply` already created a guest at that ID, `terraform destroy` the partial first. **Not rolled back:** a Vault re-init's recovery keys and root token (1Password already updated, Path B), and any Synology LUNs you deleted. On Path A, Vault's data is the snapshot, independent of VM state.

---

## Appendix A: partial rebuilds

One control plane or one worker: see **K3s node rebuild**.

## Appendix B: when you spot an IaC gap

The purpose of a from-scratch rebuild is surfacing "I forgot to put X in IaC". When you hit one:

1. **Stop and document**; do not silently hand-fix (that is how gaps persist).
2. **Non-blocking:** finish the rebuild, file a backfill item in `docs/operations/open-questions.md`.
3. **Blocking:** hand-fix to unblock, and commit a follow-up that captures the fix in IaC.

Watch especially for anything configured with `kubectl` or `vault write` since the last rebuild, manual KPN/UCG/Synology drift, and operator-minted Vault KV with no Terraform minter (its only copy is the 1Password mirror).

## Appendix C: Munin Tailscale subnet router (DSM 7)

Adds Munin to the tailnet as a K3s-independent break-glass subnet router. The auth key is minted in the Tailscale admin UI and kept in 1Password (not Vault, not auto-renewing).

> **Bring-up must use the CLI auth key, never the DSM web-UI login.** The UI applies default flags (accept-routes, exit-node consumption) that route Munin's own `10.0.254.x` traffic into the tailnet and **cut the NAS's own LAN** (killing iSCSI and NFS to the cluster).

1. `terraform/tailscale/policy.hujson` must list `autogroup:admin` as an owner of `tag:subnet-router`; `terraform -chdir=terraform/tailscale apply`.
2. Mint an auth key in the admin UI: reusable off, ephemeral off, pre-approved on, tag `tag:subnet-router`. Store it in 1Password.
3. Install **Tailscale** from DSM Package Center. Do not log in through the UI.
4. DSM Task Scheduler → Boot-up task as root: `/var/packages/Tailscale/target/bin/tailscale configure-host` then `synosystemctl restart pkgctl-Tailscale.service` (TUN permissions; wiped on every package upgrade).
5. Run it live once (skip a reboot, iSCSI PVCs are attached): `ssh admin@10.0.254.20`, then `sudo /var/packages/Tailscale/target/bin/tailscale configure-host && sudo synosystemctl restart pkgctl-Tailscale.service`.
6. Bring up (the `--reset` cures any stray UI-set flag):

   ```bash
   sudo tailscale up --authkey="$(op read '<key item>')" --advertise-routes=10.0.0.0/16 \
     --accept-routes=false --accept-dns=false --hostname=munin --reset
   ```

7. Verify in the admin UI: connected, tagged, advertising `10.0.0.0/16` approved, no "cannot relay traffic" warning.

**Break-glass** if an upgrade breaks routing or the UI cut the LAN: reach Munin on its **Tailscale** IP (still in the tailnet) and `sudo tailscale down` to restore the LAN immediately. Do **not** power-cycle (boot re-applies the bad saved state). Then repeat steps 5 and 6.

## Appendix D: why the order is not "run site.yml"

- **Vault ↔ AppRole.** Every LXC role reads secrets from Vault through an AppRole; Vault runs inside K3s. So K3s and Vault (§3) must be fully up before any LXC play.
- **Vault ↔ AdGuard ↔ DNS.** AdGuard needs Vault, which needs K3s, whose nodes' default resolver is AdGuard. Break the circle with the temporary resolver in §3.2, then re-converge in §4.
- **etcd before Patroni.** Patroni's DCS is etcd.
- **PBS is a data-restore dependency.** If it is also down, rebuild it right after Munin, not at the end.
- **A Vault snapshot restores KV, auth, policies and roles, not external-provider state.** Cloudflare, Authentik, Tailscale, NetBox, Garage are recreated by re-applying their modules (§7) on either path.

---

## See also

- **K3s node rebuild**, **Vault recovery**, **Postgres operations** and **Backups & restore drills** (this section).
- **Compute & hypervisors** (Components): what is being rebuilt.
- **Identity & secrets** (Components): the bootstrap secret model.
