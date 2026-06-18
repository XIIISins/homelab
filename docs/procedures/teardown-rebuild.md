<!-- docs/procedures/teardown-rebuild.md -->

# Disaster recovery & full rebuild — homelab

> The canonical DR + rebuild procedure. **Mirrored as a PDF in 1Password (+ iCloud/Dropbox)** so it's reachable when the homelab — including Munin and the wiki — is down. Contains **no secrets, only pointers.**
>
> Supersedes the 2026-05-17 asgard-K3s-only rebuild runbook, archived at [`archive/2026-05-17-asgard-rename-rebuild.md`](archive/2026-05-17-asgard-rename-rebuild.md) (still the validated reference for the *K3s-cluster-only* rebuild path).

---

## Disaster-recovery entry point — start here if everything is down

The recovery chain has four legs, **none of which depend on the homelab**:

1. **The IaC** — `git@github.com:XIIISins/homelab.git` (private). Clone to `~/Dev/xiiisins/homelab`. Complete declarative spec: Terraform + Ansible + Flux.
2. **The bootstrap secrets** — 1Password **"Homelab 2.0"** vault. To rebuild from zero you need:
   - `[Bootstrap] - Manual - Vault - Root token` + `[Bootstrap] - Manual - Vault - Recovery keys` — Vault root + unseal
   - `[Bootstrap] - Manual - AWS - KMS unseal access key` + `[Bootstrap] - Manual - Vault - KMS unseal config` — auto-unseal (region `eu-west-1` + key ARN; the ARN embeds the AWS account ID, kept in 1P not git)
   - `[Bootstrap] - Manual - Sealed Secrets - Master keys` — decrypts every SealedSecret; **apply BEFORE the sealed-secrets controller starts** (§3.4)
   - `[Bootstrap] - Manual - Flux - Deploy key` — reuse so `flux bootstrap` doesn't orphan the GitHub deploy key (§3.3)
   - `[Asgard] - Ansible - Vault - AppRole (ansible-local)` — Ansible→Vault runtime creds (`username`=RoleID, `password`=SecretID)
   - `[Asgard] - Manual - Ansible - Vault password` — decrypts `group_vars/all/vault.yml`
   - `[Infra] - ansible - SSH - Private key` + `[Infra] - recovery - SSH - Private key` — node + break-glass access
   - `[Infra] - Terraform - Proxmox - API token` (+ `Root password` for ticket-auth LXCs — `terraform/proxmox/asgard-lxcs-root/`)
   - `[Asgard] - Terraform - AWS - State access key` (+ `Bootstrap access key` for the `aws/` module — see §2)
   - `[Infra] - Manual - Synology - Admin login` + `[Infra] - Manual - Synology - kubernetes user` + `[Infra] - Manual - UCG-Ultra - Admin login` — foundation config (§1). (KPN has no 1P item — it's consumer hardware with no useful API; its config lives in [`../architecture/network.md`](../architecture/network.md).)
   - `[Asgard] - Manual - K3s - Kubeconfig (asgard)` — cluster access once it's up
3. **This runbook** — the procedure below.
4. **A control node** — any machine with `op`, `terraform`, `ansible`, `kubectl`, `vault`, `flux`, `kubeseal`, `jq`, `fish`. Load creds via the `homelab-env` shim (`. ~/.cache/homelab/env.sh`, or `homelab-env` in fish); AppRole bootstrap in [`../architecture/identity-secrets.md`](../architecture/identity-secrets.md).

**Reference the offline mirror, not memory.** Every credential above lives in 1Password; fetch dynamically (`op read …`, `vault kv get …`) — never type a literal secret into a shell or this document.

---

## Scope & starting assumptions

**This guide rebuilds the entire homelab from a single assumed-standing layer: the Proxmox hosts.**

| Assumed already up (NOT rebuilt here) | Rebuilt by this runbook |
|---|---|
| Proxmox PVE on Urd/Verd/Skuld, cluster `niflheim` formed, VM/LXC templates present | Everything else: network config, Synology config, all LXCs, asgard K3s, all Terraform-managed resources, all Flux workloads |

Everything below the Proxmox layer that *isn't* in IaC (UCG-Ultra, KPN, Synology base config) is rebuilt **manually** in §1, guided by [`../architecture/network.md`](../architecture/network.md) and [`../services/synology.md`](../services/synology.md). Everything in IaC is rebuilt by re-applying Terraform / re-running Ansible / letting Flux reconcile.

**Validation status — be honest with yourself mid-rebuild.** The **asgard-K3s-cluster path** (§3) is end-to-end validated (2026-05-17; partial CP/worker swaps validated 2026-05-22). The **foundation, LXC-tier, and data-restore paths** (§1, §4–§6) are derived from the live IaC + design docs and have **not** been exercised as a single cold rebuild. Treat the ordering as correct-by-construction but watch for gaps; when you hit one, see [Appendix E](#appendix-e--when-you-spot-an-iac-gap).

### Dependency / bring-up order

```
§1 Foundation (manual)        Network (UCG/KPN) ─┐
                              Synology (Munin)  ─┤
                                                 ▼
§2 Terraform state            S3 backend reachable, identities loaded
                                                 ▼
§3 Asgard K3s core            VMs → Ansible(K3s) → Flux → Vault(restore) → infra+storage
                                                 ▼
§4 Network / identity LXCs    AdGuard (DNS) → Tailscale  ── need Vault (§3) for secrets
                                                 ▼
§5 Data tier                  etcd DCS → Patroni PG → DB data restore
                                                 ▼
§6 App / monitoring LXCs      Factorio → Hermod → Zabbix(Hugin) → PBS
                                                 ▼
§7 Secret-minting TF + apps   cloudflare/authentik/tailscale/netbox/garage/semaphore/… → Flux apps converge
                                                 ▼
§8 Fleet agents               vlagent + zabbix-agent + Semaphore templates
                                                 ▼
§9 Verification
```

The order is the steady-state converge order encoded in [`ansible/playbooks/site.yml`](../../ansible/playbooks/site.yml), adjusted for cold-boot dependencies (etcd before Patroni; the DNS and Vault↔AppRole circular breaks called out inline — full rationale in [Appendix D](#appendix-d--bootstrap-ordering-rationale)). **Day-1 bring-up uses targeted `--limit` plays, never `site.yml`** (site.yml is the steady-state tool and asserts a ≥20-host inventory).

### Time budget & two-day split

Times are wall-clock for one operator working the happy path; add slack for first-cold-rebuild discovery.

| § | Section | Est. | Day |
|---|---|---|---|
| 0 | Control node + secrets bootstrap | 20–30 min | **1** |
| 1.1 | Network — UCG-Ultra + KPN (manual UI) | 30–60 min | **1** |
| 1.2 | Synology (Munin) — volumes/shares/NFS/iSCSI/user/Tailscale (manual) | 60–120 min | **1** |
| 2 | Terraform state sanity | 10 min | **1** |
| 3.1 | K3s VMs (Terraform) | 10 min | **1** |
| 3.2 | K3s bootstrap (Ansible) | 25–35 min | **1** |
| 3.3 | Flux bootstrap + deploy key | 10 min | **1** |
| 3.4 | Sealed-secrets keypair restore ⚠️ | 5 min | **1** |
| 3.5 | Vault recovery (snapshot or fresh init) | 20–45 min | **1** |
| 3.6 | Vault TF config + AppRole SecretIDs | 15 min | **1** |
| 3.7 | Infra + storage-tier reconcile | 20–40 min | **1** |
| 4 | AdGuard + Tailscale LXCs | 30–45 min | **2** |
| 5 | Data tier (etcd → Patroni → DB restore) | 45–75 min | **2** |
| 6 | Factorio + Hermod + Zabbix + PBS LXCs | 60–90 min | **2** |
| 7 | Secret-minting TF modules + Flux apps | 30–60 min | **2** |
| 8 | Fleet agents + Semaphore | 15 min | **2** |
| 9 | Verification | 30 min | **2** |

**Day 1 — critical path / foundation (≈4–6 h).** §0 → §3. End state: foundation up, asgard K3s 3+3 Ready, Vault restored + 3/3 voters, infra Kustomizations healthy. **Clean pause point** — safe to walk away indefinitely; no half-built quorum-sensitive state.

**Day 2 — services on top (≈4–6 h).** §4 → §9. LXC tiers, PG data, app reconcile, verification.

> ⚠️ **Do not pause inside §3.2 (Ansible mid-play), §3.5 (Vault init mid-flight), or §5 (Patroni bootstrap).** Those leave half-formed clusters. Finish the section or revert it.

---

## Section 0 — Control node + secrets bootstrap

~20–30 min. Goal: a shell that can reach AWS (state), Proxmox, and (later) Vault.

```fish
# Tools: op, terraform, ansible, kubectl, vault, flux, kubeseal, jq, fish.
# On a fresh Mac the Bash-tool / non-interactive PATH may miss Homebrew —
# prefix with PATH="/opt/homebrew/bin:$PATH" if a binary is "not found".

git clone git@github.com:XIIISins/homelab.git ~/Dev/xiiisins/homelab
cd ~/Dev/xiiisins/homelab

# 1Password CLI signed in
op vault list | grep -i 'Homelab 2.0'

# Control-node fish tooling (loads VAULT_ADDR + ANSIBLE_HASHI_VAULT_* + AWS_* + … from 1P)
ln -sf (pwd)/.config/fish/conf.d/homelab.fish ~/.config/fish/conf.d/homelab.fish
homelab-env            # caches to ~/.cache/homelab/env.{sh,fish}, 24h TTL

# Ansible deps
ansible-galaxy collection install -r ansible/requirements.yml
pipx inject ansible hvac           # if Ansible is pipx-installed
set -Ux OBJC_DISABLE_INITIALIZE_FORK_SAFETY YES   # macOS fork-safety, one-time

# SSH keys for node access (ansible + recovery break-glass) from 1P
op read 'op://Homelab 2.0/e5mkz32pdzde7ouxq62w3hajx4/private key' > ~/.ssh/homelab_ansible   # [Infra] - ansible - SSH - Private key
op read 'op://Homelab 2.0/zekpfaskgxceks32yqitamzzqe/private key' > ~/.ssh/homelab_recovery  # [Infra] - recovery - SSH - Private key
chmod 600 ~/.ssh/homelab_*

# Ansible Vault password file (decrypts group_vars/all/vault.yml)
op read 'op://Homelab 2.0/wte7tqcuwh5b32ljrh4jnmbcpy/password' > ~/.config/ansible/vault-pass  # [Asgard] - Manual - Ansible - Vault password
chmod 600 ~/.config/ansible/vault-pass
```

`VAULT_TOKEN` is NOT loaded yet (Vault is down). It comes in §3.5.

---

## Section 1 — Foundation (manual, not in IaC)

Proxmox is up; the network and Synology are not. Both are consumer/appliance config with no useful API — they live in docs, not IaC.

### 1.1 Network — UCG-Ultra + KPN  (~30–60 min)

Restore from [`../architecture/network.md`](../architecture/network.md). Key state to recreate in the UniFi UI:

- **VLANs** (ID + subnet must match exactly — everything downstream is hard-coded to these):

  | VLAN | Subnet | Name |
  |---|---|---|
  | 1 | `10.0.254.0/24` | HL-MGMT |
  | 10 | `10.0.10.0/24` | HL-ASG-VIP |
  | 11 | `10.0.11.0/24` | HL-ASG-SVC |
  | 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP |
  | 21 | `10.0.21.0/24` | HL-ASG-K3S-NODE |
  | 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP |
  | 31 | `10.0.31.0/24` | HL-JOT-K3S-NODE |
  | 60 | `10.0.60.0/24` | HL-CLIENT |
  | 100 | `10.0.100.0/24` | HL-STOR |
  | 222 | `10.0.222.0/24` | Untrusted |

- **Zone firewall** — all VLANs in the Internal zone; `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). UCG is the sole firewall boundary.
- **Port-forwards (UCG only)** — recreate for every externally-exposed service. Today: Teamspeak voice (UDP 9987) + filetransfer (TCP 30033) → MetalLB `10.0.20.12`; and any other service whose HTTPRoute is external via cloudflared is reached *through the tunnel*, not a port-forward (cloudflared dials out — no inbound rule needed). SFTP/game for Factorio if exposed.
- **KPN Experia Box** — "exposed host" / DMZ mode forwarding all unsolicited inbound (IPv4 **and** IPv6 tabs) to the UCG WAN IP. KPN keeps outbound NAT for `192.168.2.0/24` only.

**Verify before flipping the DMZ:** confirm the `Any → Any: Deny` rule exists.

### 1.2 Synology (Munin) — storage  (~60–120 min)

Restore from [`../services/synology.md`](../services/synology.md) and [`synology-storage-redesign.md`](synology-storage-redesign.md) (the latter is the *current* tiered layout). DSM is manual UI. Recreate:

- **Volumes** on the RAID-1 pool — Volume1 (data / backups), Volume2 (media + Garage meta). The 2026-05-30 redesign made **Volume1 the dedicated backup volume**; mind the **DSM-wide ~10 iSCSI LUN cap** (hard model limit — see CLAUDE.md). Storage tiering is what keeps you under it: iSCSI only for block-critical single-instance data; NFS for file-class; local-path (node-NVMe) for app-replicated; emptyDir for caches.
- **Shared folders + exports** — `proxmox-backup` (NFS, PBS datastore), `db-backups` (NFS), `uploads` (Factorio SFTP), `media`/`manga`/`downloads`/`immich`, plus the NFS exports backing the K3s file-class tier (`csi-driver-nfs`). Match the export paths the redesign moved things to (`/volumeN/…`) — a stale path = stale NFS handle on clients.
- **iSCSI** — SAN Manager installed; Synology CSI auto-creates one target+LUN per PVC at provision time, so **no manual LUNs to pre-create** — just the SAN Manager service.
- **`kubernetes` user** (admin) — consumed by Synology CSI. Credentials in 1P; the SealedSecret in `synology-csi-config/` carries them into the cluster.
- **Tailscale subnet-router** (DSM Package Center) — see [Appendix C](#appendix-c--munin-tailscale-subnet-router-install-dsm-7). Optional at this stage; needed for K3s-independent break-glass.

**Reboot-test** the Synology after volume/share/export changes before depending on it (boot-mount failures hide until next reboot).

---

## Section 2 — Terraform state sanity  (~10 min)

State lives in S3 (`xiiisins-homelab-tfstate`, eu-west-1, native lock) — **off-homelab, so it survives a full homelab wipe.** That means each module's state still references the now-destroyed resources; `terraform apply` will refresh, find them gone, and recreate. For *imported* resources (AdGuard LXCs, NetBox records) the `import {}` blocks / `lifecycle.ignore_changes` already in the modules handle re-creation.

```fish
# The aws/ module (state bucket + IAM) is local-state and uses the BOOTSTRAP identity, not the runtime one
set-aws-creds bootstrap          # fish shim; aws/ needs s3:GetBucketPolicy which the runtime identity lacks
cd terraform/aws
terraform init && terraform plan  # expect no changes (bucket + IAM survived in AWS)

# Everything else uses the terraform-state runtime identity (the homelab-env default)
set-aws-creds terraform-state    # or just `homelab-env`
```

If the S3 bucket itself is gone (whole AWS account lost), that's a different DR — `terraform/aws/` recreates the bucket but every other module's state is gone with it, turning every `apply` into a from-scratch create (and Vault snapshot-restore in §3.5 becomes the only way back to the KV data). Note it and proceed; the modules are written to create-from-empty.

> **Apply discipline:** `terraform apply` runs only from the main checkout (state-lock serialises writes; this preserves intentionality). `plan` from anywhere is fine. Re-verify `VAULT_TOKEN` before each `vault_*` module apply on a long day — it expires mid-flight.

---

## Section 3 — Asgard K3s core

The cluster everything else talks to. This section is the validated path (see archive). End state: 3+3 Ready, Vault 3/3 voters, infra healthy.

### 3.1 K3s VMs — Terraform  (~10 min)

```fish
cd terraform/proxmox/asgard-k3s
terraform init && terraform apply      # creates VMs 2001–2003 (CP) + 2101–2103 (workers)
```

Each worker carries a 50G `scsi1` data disk (`/data`, local-path tier — see CLAUDE.md). Cloud-init takes 2–3 min after apply returns. Clear stale host keys + confirm reachability:

```fish
for ip in 10.0.21.11 10.0.21.12 10.0.21.13 10.0.21.21 10.0.21.22 10.0.21.23
    ssh-keygen -R $ip
    ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no root@$ip hostname
end
```

### 3.2 K3s bootstrap — Ansible  (~25–35 min)

> ⚠️ **Cold-boot DNS break.** The node `baseline_nameservers` default is `[10.0.10.200 (AGH VIP), 10.0.254.1 (UCG)]` — **both dead** until AdGuard comes up in §4, yet the K3s/Calico install needs working DNS to pull images. Break the circle by overriding to a temporary resolver for the bootstrap run only, then re-converge to AGH defaults in §4. (Confirm whether your UCG runs an independent resolver; if not, use a public one temporarily.)

```fish
cd ~/Dev/xiiisins/homelab/ansible

# Day-1 baseline as root (hardening later locks root SSH out). Temporary DNS override.
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml \
    -e 'ansible_user=root' -e '{"baseline_nameservers":["1.1.1.1","10.0.254.1"]}' \
    --tags baseline

# Full deploy as ansible user (baseline → k3s → hardening). Init node = gondul by default.
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml \
    -e '{"baseline_nameservers":["1.1.1.1","10.0.254.1"]}'
```

`detect-state.yml` gates install on health, so re-runs are safe. The init node (`gondul`) `--cluster-init`s; the others join. Kubeconfig is fetched to `~/.kube/niflheim-asgard.yaml`.

```fish
set -x KUBECONFIG ~/.kube/niflheim-asgard.yaml
kubectl get nodes -o wide          # expect 3 CP + 3 worker, all Ready
kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}{"\n"}'
# expect cidrs=[10.0.21.0/24]  (NOT firstFound — the multi-homed-worker landmine)
```

### 3.3 Flux bootstrap + deploy key  (~10 min)

**Restore the deploy-key Secret first** (`[Bootstrap] - Manual - Flux - Deploy key`) so bootstrap reuses it instead of orphaning the GitHub deploy key:

```fish
kubectl create namespace flux-system
op read 'op://Homelab 2.0/v4swe4y3i55oawjlsc3rcatmvu/notesPlain' | kubectl apply -f -   # [Bootstrap] - Manual - Flux - Deploy key

flux bootstrap github \
    --owner=XIIISins --repository=homelab --branch=main \
    --path=./k8s/asgard/flux-system --personal --private \
    --kubeconfig ~/.kube/niflheim-asgard.yaml
```

### 3.4 Sealed-secrets master keypair restore ⚠️ CRITICAL  (~5 min)

**Must happen between Flux bootstrap and the sealed-secrets controller's first reconcile.** If the controller starts first it generates a *new* keypair and every SealedSecret in Git (`vault-unseal`, `synology-csi`, …) becomes undecryptable.

```fish
op read 'op://Homelab 2.0/mv4u6dyzjkzx6aogvxbmxz3vcm/notesPlain' | kubectl apply -f -   # [Bootstrap] - Manual - Sealed Secrets - Master keys
kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active
# expect ≥1 Secret listed
```

If you raced the controller: re-seal each SealedSecret from plaintext (`vault-unseal` plaintext is in `group_vars/all/vault.yml`; `synology-csi` creds in 1P) against the controller's new cert (`kubeseal --fetch-cert`), commit, push, reconcile.

### 3.5 Vault recovery  (~20–45 min)

Vault comes up via its HelmRelease but is **uninitialized** (fresh local-path PV). KMS auto-unseal config (region `eu-west-1` + key ARN) comes from the `vault-unseal` SealedSecret you restored in §3.4.

> **Storage note:** Vault is on the `local-path` tier now (node-NVMe, one PV pinned per worker), not iSCSI. HA is app-level Raft quorum; PBS backs up each worker's `/data`.

#### Path A — Raft snapshot restore (PRIMARY; preserves all KV + auth + policies + AppRole roles)

Use when a recent snapshot exists at `~/homelab-backups/vault/` (the documented backup; `vault operator raft snapshot save`).

```fish
kubectl get pods -n vault -w        # wait for vault-0 Running (sealed, 0/1)

# Init a fresh barrier so the pod is unsealed (KMS), then restore the old data over it.
kubectl exec -n vault vault-0 -- vault operator init \
    -recovery-shares=5 -recovery-threshold=3 -format=json \
    > ~/homelab-rebuild-state/vault-init-tmp.json
kubectl exec -n vault vault-0 -- vault status     # Sealed:false (KMS auto-unseal)

# Restore the snapshot (re-encrypts under the current KMS key; recovery keys + root
# token revert to the ORIGINAL snapshot's — i.e. the 1P bootstrap values).
kubectl cp ~/homelab-backups/vault/<latest>.snap vault/vault-0:/tmp/vault.snap
kubectl exec -n vault vault-0 -- env \
    VAULT_TOKEN=(op read 'op://Homelab 2.0/7g4grolyien2yqkm7me2jficmy/password') \
    vault operator raft snapshot restore /tmp/vault.snap
kubectl exec -n vault vault-0 -- rm /tmp/vault.snap
```

After restore, KV (30+ entries), policies, auth methods (kubernetes + approle), and both AppRole *roles* are back. AppRole *SecretIDs* are NOT in the snapshot (they were never stored in Vault state that way) — re-mint in §3.6. Snapshot does **not** restore external-provider state (Cloudflare records, Authentik objects, Tailscale ACL, NetBox rows) — those come from re-applying their TF modules in §7.

#### Path B — Fresh init from IaC (FALLBACK; no usable snapshot)

```fish
kubectl exec -n vault vault-0 -- vault operator init \
    -recovery-shares=5 -recovery-threshold=3 -format=json \
    > ~/homelab-rebuild-state/vault-init.json
```

**Update 1Password** with the new root token + 5 recovery keys (replace the bootstrap entries, tag with rebuild date). Then §3.6 re-applies `terraform/vault/` to recreate auth/policies/roles/KV-engine, and **every secret-minting TF module in §7 re-mints its KV entries** — operator-minted KV (Discord webhooks, SP keypairs, etc.) must be re-written by hand from 1P mirrors. This path rotates everything and ripples through every downstream consumer; prefer Path A.

#### Raft followers (both paths)

```fish
# vault-1/2 often miss the auto-join window → manual join, then KMS auto-unseals them (~30s)
kubectl exec -n vault vault-1 -- vault operator raft join http://vault-0.vault-internal:8200
kubectl exec -n vault vault-2 -- vault operator raft join http://vault-0.vault-internal:8200
kubectl exec -n vault vault-0 -- vault operator raft list-peers   # 3 members, all voter
```

If init hit a stuck partial state (`stored unseal keys are supported, but none were found`): `kubectl delete sts vault -n vault --cascade=orphan` → `delete pvc data-vault-{0,1,2}` → `delete pod vault-{0,1,2} --force` → `flux reconcile hr vault -n vault --force`, then retry.

### 3.6 Vault TF config + AppRole SecretIDs  (~15 min)

```fish
fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'   # mint VAULT_TOKEN from 1P
vault token lookup                                                   # verify

cd terraform/vault
terraform init && terraform plan
# Path A: near-no-op (config already present from the snapshot — apply to reconcile any drift)
# Path B: large additions (KV engine, k8s auth + config, approle + roles, eso policy/role)
terraform apply
```

Re-mint AppRole SecretIDs (never in TF state, never in the snapshot):

```fish
# ansible-local (this control node) — store new SecretID in 1P item "Ansible - Vault - k3s"
vault write -f auth/approle/role/ansible-local/secret-id     # paste secret_id + accessor into 1P (NOT username — that's the stable RoleID)
homelab-env --refresh

# ansible-awx (consumed by Semaphore) — Vault-KV-canonical, comes later with Semaphore (§7)
# Test the lookup path end-to-end:
set-vault-token approle
ansible-playbook -i inventory/hosts.yml playbooks/test-vault-lookup.yml   # expects a value back
set -e VAULT_TOKEN
```

### 3.7 Infrastructure + storage-tier reconcile  (~20–40 min)

Flux drives the rest of `infrastructure/`. Watch it converge:

```fish
flux get all -A --watch
```

Expected dependency order: `infrastructure` (sealed-secrets, ESO, MetalLB, synology-csi, csi-driver-nfs, local-path-provisioner, tigera no-op, traefik, gateway-api, cert-manager, cloudflared, garage, observability) → the `*-config` Kustomizations (`infrastructure-config` ESO ClusterSecretStore, `metallb-config`, `vault-config`, `synology-csi-config`, `cert-manager-config`, `gateway-config`).

Verify the load-bearing pieces:

```fish
kubectl get clustersecretstore vault -o jsonpath='{.status.conditions[0].status}{"\n"}'   # True
kubectl get sc                                          # synology-csi-iscsi-retain(default), local-path, nfs-*
kubectl get pods -n metallb-system,traefik,cert-manager,garage,monitoring

# MetalLB VIP reachable from OUTSIDE the cluster (proves route_localnet + VLAN20 policy routing + L2)
curl -s -o /dev/null -w "%{http_code}\n" --max-time 5 http://10.0.20.10/    # Traefik VIP; any HTTP code = path works
# (MetalLB VIPs don't answer ICMP — test with TCP, never ping.)
```

The `local-path` tier needs each worker's `scsi1` `/data` disk mounted with the SELinux `context=` option (the `local-path-disk` role); confirm `kubectl get pv` shows Vault's PVs `Bound` on `local-path`.

> **Day-1 pause point.** Cluster up, Vault restored + 3/3 voters, infra healthy. Safe to stop here.

---

## Section 4 — AdGuard + Tailscale LXCs  (~30–45 min)

These need Vault (their Ansible roles look up `secret/ansible/adguard/*`, `secret/ansible/tailscale/*` via the `ansible-local` AppRole). Vault is up → the circle is broken.

```fish
# AdGuard LXCs (Saga/Mimir/Kvasir) — TF import-aware module + Ansible
cd terraform/proxmox/asgard-lxcs && terraform apply        # creates the 3 AGH LXCs (+ all asgard-lxcs)
cd ~/Dev/xiiisins/homelab/ansible
set-vault-token approle
ansible-playbook -i inventory/hosts.yml playbooks/asgard-adguard.yml
# adguard_force_overwrite_config:false preserves operator state on re-runs; first cold deploy
# seeds rewrites from terraform/adguard/ in §7.

# Tailscale LXCs (Bifrost/Heimdall/Gjallarbru) — device_passthrough needs the root-pam module
cd terraform/proxmox/asgard-lxcs-root && PROXMOX_VE_PASSWORD=(op read 'op://Homelab 2.0/6vv32uzlahikgmkvkiqfnkgshy/password') terraform apply  # [Infra] - Terraform - Proxmox - Root password
cd ~/Dev/xiiisins/homelab/ansible
ansible-playbook -i inventory/hosts.yml playbooks/asgard-tailscale.yml
```

> **Tailscale authkeys cap at 90 days.** If the last `terraform/tailscale/` apply was >90d ago, re-apply it (§7) **before** the Ansible run — otherwise Vault holds an expired key and `tailscale up` fails.

**Re-converge node DNS to AdGuard.** With AGH serving, drop the temporary resolver override and re-run baseline to restore the AGH-VIP `baseline_nameservers` default:

```fish
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --tags baseline   # no -e override now
kubectl rollout restart deploy -n kube-system coredns                              # pick up new node resolv.conf
```

---

## Section 5 — Data tier (etcd → Patroni → DB restore)  (~45–75 min)

> **Cold-boot order differs from `site.yml`.** site.yml lists postgres before haproxy-etcd (steady-state safe), but Patroni's DCS is etcd — **etcd must be up first** on a cold boot.

### 5.1 HAProxy + etcd trio (Hlin/Eir/Snotra)

```fish
# LXCs created by asgard-lxcs apply in §4. Bring up etcd DCS + HAProxy + keepalived VIP.
ansible-playbook -i inventory/hosts.yml playbooks/asgard-haproxy-etcd.yml
# Verify etcd quorum (3 members) before touching Patroni.
```

> ⚠️ **One Ansible playbook at a time across all agents** — concurrent runs race on the `restart etcd` handler (quorum loss). This play has no `serial:`; don't force-handlers it.

### 5.2 PostgreSQL / Patroni trio (Fulla/Vör/Idunn)

```fish
ansible-playbook -i inventory/hosts.yml playbooks/asgard-postgres.yml
sudo -n true; ssh ansible@10.0.11.230 'sudo patronictl -c /etc/patroni/patroni.yml list'   # one Leader, two replicas streaming
```

Patroni bootstraps the leader against etcd, then basebackups the replicas. The HAProxy VIP `10.0.10.210` routes writes to whichever node `/master` returns 200.

### 5.3 Restore database data

A fresh Patroni cluster is **empty** — the app DBs (authentik, netbox, semaphore, zabbix, teamspeak3, outline) must be restored. Backup source: the per-node daily logical dumps at `/var/backups/postgresql` (`globals.sql` + `<db>.dump`, leader-gated, **PBS-captured** — local disk, never NFS). Recover the dump files via a PBS file-restore of the most recent PG-leader backup, then:

```fish
# On the current Patroni leader, per database (globals FIRST):
psql -h /var/run/postgresql -f /var/backups/postgresql/globals.sql
createdb -h /var/run/postgresql <db>
pg_restore -h /var/run/postgresql -d <db> /var/backups/postgresql/<db>.dump
```

App roles/passwords come from Vault (`postgres-common` provisions them against the **current leader** — `--limit fulla` is unsafe, leadership floats; discover the leader via `patronictl list`).

> **Alternative — wholesale LXC restore from PBS.** Instead of rebuilding the data tier from IaC + logical restore, you can restore the three PG LXCs directly from PBS (captures the Patroni data dir + dumps). Faster to last-known-good, but skips the IaC-validation the cold rebuild is meant to exercise, and the etcd DCS state must still be consistent. Operator's call per the time budget. **No WAL archiving / PITR** today (scaffolded no-op `archive_command`); effective history is one day of daily restore points.

---

## Section 6 — Application + monitoring LXCs  (~60–90 min)

All depend on Vault (secrets) and most on PG (VIP). Bring up in any order within the section.

```fish
ansible-playbook -i inventory/hosts.yml playbooks/asgard-factorio.yml   # game LXC 1120 (SFTPGo + reconcile loop)
ansible-playbook -i inventory/hosts.yml playbooks/asgard-hermod.yml     # notifications LXC 1103 (AppriseAPI + Caddy)
ansible-playbook -i inventory/hosts.yml playbooks/asgard-zabbix.yml     # Hugin LXC 1102 (Zabbix 7.0 server + frontend)
```

**Zabbix DB** is one of the §5.3 restores; **first-login admin** is factory `Admin`/`zabbix` until rotated to the Vault value via UI (see CLAUDE.md Zabbix gotchas — don't pre-login with the Vault value, it locks the account).

### 6.x PBS LXC (1101 on Skuld)

PBS was *assumed up* as the restore source for §5.3 / rollback. If PBS itself is also gone, it's a hard bootstrap dependency for data restore — rebuild it early (right after §1.2) so it's available:

```fish
ansible-playbook -i inventory/hosts.yml playbooks/asgard-pbs.yml        # agent-only converge; datastore is NFS on Munin
```

The datastore is the `proxmox-backup` NFS export on Munin (§1.2). After mount, `systemctl restart proxmox-backup proxmox-backup-proxy` and **restore-verify** (a real file restore) before relying on it.

---

## Section 7 — Secret-minting Terraform modules + Flux apps  (~30–60 min)

These re-create external-provider resources (Cloudflare DNS, Authentik apps/users, Tailscale ACL/DNS, NetBox records, Garage bucket/key, Semaphore project, AGH rewrites, Proxmox Zabbix token) **and** write their machine secrets into Vault KV. On **Path A** (snapshot) the KV writes are no-ops/reconciles but the external resources still need re-creating; on **Path B** they're the only source of those KV entries.

Apply in dependency order (all from the main checkout):

```fish
# Vault config already applied (§3.6). Now the minting modules:
terraform -chdir=terraform/cloudflare apply    # tunnel + DNS records + cloudflared creds (secret/k8s/cloudflared/credentials)
terraform -chdir=terraform/authentik apply     # OIDC/SAML providers + identity-as-data (users.yaml/groups.yaml)
terraform -chdir=terraform/tailscale apply     # ACL (policy.hujson) + auth keys + split DNS
terraform -chdir=terraform/netbox apply -parallelism=1   # ~160 records; parallelism=1 avoids the transient 500s
terraform -chdir=terraform/garage apply        # bucket+key+grant + secret/k8s/outline/s3 (needs `kubectl port-forward svc/garage-admin 3903`)
terraform -chdir=terraform/semaphore apply     # pushes ansible-awx SecretID + app/oidc/pg config to Semaphore
terraform -chdir=terraform/adguard apply       # DNS rewrites (write-to-origin Saga; sync fans to Mimir/Kvasir)
terraform -chdir=terraform/proxmox/zabbix-access apply   # PVE user/token for Zabbix HTTP scrape
```

For **Path B** also re-mint `ansible-awx` via `rotate-semaphore-approle`, and hand-restore any operator-minted KV from 1P mirrors (Hermod Discord webhooks `secret/ansible/hermod/discord/*`, Zabbix SAML SP keypair, etc.).

Then the Flux **apps** (`k8s/asgard/apps/`) converge once their ExternalSecrets resolve (authentik, netbox, outline, semaphore, teamspeak, victorialogs, victoriametrics, apex-static, zabbix-ingress):

```fish
flux reconcile kustomization apps --with-source
flux get hr -A          # all Released
# Force-sync any ExternalSecret still stale after a fresh KV write:
kubectl annotate -n <ns> externalsecret <name> force-sync=(date +%s) --overwrite
```

Apps with PG backends pick up the restored DBs automatically via the VIP. Observability data (VictoriaLogs/Metrics) is **refillable/downtime-tolerant** by design (local-path single-instance, accept-loss) — no restore step; it repopulates from live ingest.

---

## Section 8 — Fleet agents + Semaphore  (~15 min)

```fish
ansible-playbook -i inventory/hosts.yml playbooks/vlagent.yml         # log shipper, all hosts
ansible-playbook -i inventory/hosts.yml playbooks/zabbix-agent.yml    # agent2, all hosts
ansible-playbook -i inventory/hosts.yml playbooks/zabbix-host-groups.yml
```

Semaphore (deployed in §7 as a K3s app) drives steady-state from here: its `refresh-netbox-inventory` (4h), `asgard-drift-check` (6h), `asgard-apply` (manual), `asgard-fleet-agents` (daily) templates. **Drift-check baseline = zero `changed=` across the fleet** — once green, an `alert` in Hermod means real drift.

---

## Section 9 — Verification checklist  (~30 min)

**Foundation**
- [ ] All VLANs resolve + route; `Any→Any Deny` confirmed before DMZ flip
- [ ] Synology shares/exports mount from a client; iSCSI SAN Manager up; `kubernetes` user works
- [ ] PBS datastore mounted + a test file-restore succeeds

**Asgard K3s**
- [ ] `kubectl get nodes` → 3+3 Ready; `kubectl get pods -A` → nothing CrashLoopBackOff
- [ ] `flux get all -A` → all Kustomizations Ready, all HelmReleases Released
- [ ] `clustersecretstore vault` status `True`
- [ ] `vault status` initialized+unsealed on all 3; `raft list-peers` → 3 voters
- [ ] Sealed-secrets active keypair matches the 1P backup
- [ ] Traefik VIP `10.0.20.10` answers HTTP from outside the cluster (route_localnet + VLAN20 policy routing + L2)
- [ ] AppRole lookup works (`test-vault-lookup.yml`); idempotency: `asgard-k3s.yml` → `changed=0`

**LXC tiers**
- [ ] AdGuard VIP `10.0.10.200` resolving (TCP test, not ping); failover works
- [ ] Tailscale LXCs + Munin online in tailnet, advertising routes
- [ ] etcd 3-member quorum; Patroni one Leader + two streaming replicas; HAProxy VIP `10.0.10.210` routes to leader
- [ ] All app DBs restored + apps connected (Authentik login, NetBox, Outline, Semaphore, Zabbix, Teamspeak)
- [ ] Factorio reachable; Hermod end-to-end (test POST → Discord); Zabbix collecting from agents

**Apps / external**
- [ ] External hostnames resolve via cloudflared (`authentik.xiiisins.com`, `wiki.xiiisins.com`, …)
- [ ] Internal hostnames resolve via AGH→Traefik (`*.niflheim` / `*.midgard`)
- [ ] Semaphore drift-check returns `changed=0` across the fleet

Then update [`../operations/build-sequence.md`](../operations/build-sequence.md) status lines and commit any IaC gaps found (see Appendix E).

---

## Section 10 — Rollback

If a rebuild stalls past your time budget, restore last-known-good from **PBS** (the pre-existing snapshots of the VMs/LXCs). In Proxmox: Datacenter → Storage → pbs → find the backup for the VM/LXC ID → Restore.

- PBS restores keep the snapshot's original ID — if `terraform apply` already created a VM at that ID, `terraform destroy` the partial first (or restore to a new ID and adjust).
- **Not rolled back:** the Vault re-init's recovery keys/root token (1P is already updated — Path B only); any Synology LUNs deleted during rebuild (they held only re-creatable PV data + Vault is on local-path now). Vault data on **Path A** is the snapshot, independent of PBS VM state.

---

## Appendix A — Partial rebuild: single control-plane VM

Replace ONE CP (relocate, hardware swap) while the other two keep quorum. Validated 2026-05-22. Full step-list in the archived runbook, [Appendix B](archive/2026-05-17-asgard-rename-rebuild.md#appendix-b--partial-rebuild-replacing-a-single-cp). Essence:

```fish
# 1. (optional) snapshot the two surviving CPs
# 2. terraform apply --target='proxmox_virtual_environment_vm.control_plane["<name>"]'
kubectl delete node <name>          # ⚠️ CRITICAL — evicts stale etcd member; avoids "duplicate node name"
ssh-keygen -R <ip>
# 3. run the play; OVERRIDE init node ONLY if rebuilding the default init node (gondul):
ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'
kubectl get nodes && kubectl exec -n vault vault-0 -- vault operator raft list-peers   # back to full
```

---

## Appendix B — Partial rebuild: single worker VM (stateful)

Replace ONE worker (template/spec fix, hardware swap) while the rest run. Validated 2026-05-22 (~25 min). Full step-list in the archived runbook, [Appendix C](archive/2026-05-17-asgard-rename-rebuild.md#appendix-c--partial-rebuild-replacing-a-single-worker-stateful). Essence:

- Workers carry stateful iSCSI/local-path PVCs and are NOT etcd members.
- Vault's chart uses **required** pod anti-affinity (3 replicas / 3 workers) — there is no spare slot, so the "drain + migrate the Vault pod first" pattern can't work. **Accept 2/3 voters for the ~20–30 min window** (Vault stays read+write).
- Step Raft leadership off the doomed worker first (`vault operator step-down` while healthy), then `kubectl drain` (anti-affinity sends the displaced pod Pending), verify iSCSI/VolumeAttachment cleanup, `terraform apply --target=…worker["<name>"]`, `kubectl delete node`, `ssh-keygen -R`, run the play `--limit`. The new worker becomes Ready → the Pending Vault pod schedules onto it → rejoins Raft.

---

## Appendix C — Munin Tailscale subnet-router install (DSM 7)

Adds Munin (Synology DS223J, DSM 7) to the tailnet as a K3s-independent break-glass subnet-router. Authkey is **UI-minted + 1Password** (out-of-band — not Vault, not auto-renewing); the LXC advertisers use TF-minted Vault keys instead.

> ⚠️ **Bring-up must use the CLI authkey, NOT the DSM web-UI login** — the UI applies default flags (accept-routes / exit-node consumption) that route Munin's own `10.0.254.x` traffic into the tailnet and **cut the NAS's own LAN** (kills iSCSI+NFS to the cluster). Correct resting state pins `--accept-routes=false`. See the 2026-05-30 incident.

1. **Allow admin-minted `tag:subnet-router` keys** — `terraform/tailscale/policy.hujson` must list `autogroup:admin` as an owner of `tag:subnet-router` (`terraform apply`).
2. **Mint authkey** (Tailscale admin → Keys): reusable off, ephemeral off, pre-approved on, tag `tag:subnet-router`. Store in 1P.
3. **Install** Tailscale via DSM Package Center; don't log in via the UI.
4. **Boot-task** (DSM Task Scheduler, root, Boot-up): `/var/packages/Tailscale/target/bin/tailscale configure-host` then `synosystemctl restart pkgctl-Tailscale.service` (TUN perms; wiped on every package upgrade).
5. **Run configure-host live** (skip a reboot — iSCSI PVCs are attached): `ssh admin@10.0.254.20`, `sudo …/tailscale configure-host`, `sudo synosystemctl restart pkgctl-Tailscale.service`.
6. **Bring up** (CLI, the `--reset` is the cure for any stray UI-set flag):
   ```sh
   sudo tailscale up --authkey=tskey-auth-XXXX --advertise-routes=10.0.0.0/16 \
       --accept-routes=false --accept-dns=false --hostname=munin --reset
   ```
7. **Verify** in admin: connected, tagged, advertising `10.0.0.0/16` auto-approved, no "cannot relay traffic" warning.

**Break-glass if a package upgrade breaks routing OR the UI cut the LAN:** reach Munin over its *Tailscale* IP (still online in tailnet) and `sudo tailscale down` to restore LAN immediately — do NOT power-cycle (boot re-applies bad saved state). Then re-run step 5 + step 6.

---

## Appendix D — Bootstrap-ordering rationale

Why the cold-boot order isn't just "run `site.yml`":

- **Vault ↔ AppRole.** Every LXC Ansible role reads its secrets from Vault via the `ansible-local` AppRole. Vault runs *inside* K3s. So K3s + Vault (§3) must be fully up and populated before any LXC play (§4–§6) can run. This is why the data/identity LXCs come *after* the cluster, even though conceptually they feel like "infrastructure."
- **Vault ↔ AdGuard ↔ DNS.** AdGuard (DNS) needs Vault (admin-password-hash) → needs K3s → whose nodes' default resolver *is* AdGuard. Circular. **Break:** bootstrap K3s with a temporary resolver (`-e baseline_nameservers=[…]`, §3.2), bring AGH up (§4), then re-converge node resolv.conf to the AGH VIP. AdGuard is therefore *not* the first thing up in a cold rebuild, despite being foundational in steady state.
- **etcd before Patroni.** Patroni's DCS is the etcd trio. `site.yml` lists `asgard-postgres` before `asgard-haproxy-etcd` because in steady state both already exist; on a cold boot etcd must exist before Patroni can bootstrap (§5.1 → §5.2).
- **PBS is a data-restore dependency.** §5.3 (PG) and §10 (rollback) read from PBS. If PBS is also down, rebuild it (§6.x) immediately after Synology (§1.2), not in its `site.yml` position at the end.
- **Snapshot vs external state.** A Vault Raft snapshot restores KV + auth + policies + roles, but **not** Cloudflare/Authentik/Tailscale/NetBox/Garage external-provider resources — those are re-created by re-applying their TF modules (§7) regardless of which Vault path you took.

---

## Appendix E — When you spot an IaC gap

The point of a from-scratch rebuild is to surface "I forgot to put X in IaC" moments. When you hit one:

1. **Stop, document** — don't silently hand-fix (that's how gaps perpetuate).
2. **Non-blocking** → finish the rebuild, file a backfill task in [`../operations/open-questions.md`](../operations/open-questions.md), address after.
3. **Blocking** → hand-fix to unblock, but commit a follow-up to capture the fix in IaC.

Watch especially: anything configured imperatively via `kubectl`/`vault write` since the last rebuild; KPN/UCG/Synology manual config drift; operator-minted Vault KV with no TF minter (mirror exists only in 1P).
