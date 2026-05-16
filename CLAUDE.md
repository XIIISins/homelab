# Homelab — Claude Code context
*Read this before touching anything. Full design at `docs/homelab-design.md`.*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes. Goals:
- Reliable services for friends/family (Teamspeak, Factorio, Immich)
- Learning environment for Kubernetes and modern infrastructure
- Portfolio/resume project at senior/principal infrastructure level

Owner has 10+ years Ansible experience, career in IT/sysadmin/platform engineering. Kubernetes is the primary learning goal.

---

## Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MINISFORUM DeskMini JB95 | Celeron N5095 | 32 GB | 120GB SSD (~94GB LVM-thin) | **Urd** |
| Beelink MINI-S12 | N100 | 16 GB | 1TB (~950GB LVM-thin) | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB | ~450GB LVM-thin | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

**Hardware notes:**
- Urd (N5095) is significantly weaker than Verd/Skuld (N100). Urd should NOT run K3s control plane nodes — etcd is very sensitive to disk IO latency and the N5095 causes IO storms under load.
- Urd long-term plan: dedicated Jellyfin box with Intel QuickSync passthrough. Currently runs Einherjar-urd (K3s worker) and Göndul (CP — to be moved to Verd on next full reprovision).
- Göndul will move from Urd → Verd on next `terraform destroy && terraform apply`.
- **2026-05-14 incident context:** An etcd IO storm on Göndul (Urd/N5095) cascaded into a multi-hour outage. This is the concrete event behind "never run CP on Urd" — see Known gotchas and `homelab-design.md` incident log.

---

## Naming convention

| Thing | Name | Reason |
|-------|------|--------|
| Proxmox cluster | `niflheim` | Hidden realm |
| Proxmox node 1 | Urd | Eldest Norn, most powerful |
| Proxmox node 2 | Verd | Second Norn |
| Proxmox node 3 | Skuld | Third Norn |
| NAS | Munin | Odin's raven of memory |
| Asgard K3s CP | Göndul, Hlökk, Sigrún | Valkyries (choosers/directors) |
| Asgard K3s workers | Einherjar-urd/verd/skuld | Army of the Norns |
| Jotunheim K3s CP | Rota, Hildr, Kára | Valkyries |
| Jotunheim K3s workers | Drengr-urd/verd/skuld | Heroes of the Norns |
| AdGuard primary | Saga | Goddess of wisdom/seeing |
| AdGuard replica 1 | Mimir | Keeper of wisdom |
| AdGuard replica 2 | Kvasir | Wisest being |
| Public DNS zone | `midgard.xiiisins.com` | The known world |
| Private DNS zone | `niflheim.xiiisins.com` | The hidden realm |

---

## Critical architectural decisions — never second-guess without asking

**Two K3s clusters:**
- Asgard K3s (VLAN 21) — core infrastructure + automation + production services. Cascade-failure criterion *or* recovery-blocking criterion. Resiliency > simplicity.
- Jotunheim K3s (VLAN 31) — non-cascade-critical services + genuine experiments. Failure acceptable (hours-to-days downtime). NOT "experimental only" — most jotunheim services have real users; the line is failure-domain risk, not production/experimental.
- Do NOT suggest merging them

**Asgard K3s contents:**
- *Core infrastructure:* Vault, Authentik (server ×3 + worker ×1), Redis, MetalLB, Synology CSI, ESO, Sealed Secrets, tigera-operator, Traefik, cert-manager, Cloudflared
- *Automation:* AWX (Ansible CI), Tofu Controller (Terraform GitOps via Flux)
- *Core services:* Outline, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Netbox, n8n, Privatebin, Startpage

**Jotunheim K3s contents:** Arr stack, Komga, Homepage, wallpaper gallery, second instances of ESO and Synology CSI, plus ad-hoc experiments. Note: Startpage (personal browser homepage, daily-use) is in asgard; Homepage (service-grid dashboard) is in jotunheim — distinct services.

**No Docker Swarm. K3s only.**

**No Galera. PostgreSQL only.**
Zabbix migrated to PostgreSQL. Nothing requires MySQL.

**GitOps: Flux CD.**
Push to Git → exists. No ArgoCD. No manual `kubectl apply` for production.
Layered Flux Kustomizations: `infrastructure` installs operators/charts; per-component config Kustomizations (`infrastructure-config` for ESO, `metallb-config` for MetalLB) configure CRD-dependent resources with `dependsOn: infrastructure` so the CRDs exist first. Add a new config Kustomization per operator rather than bundling — shared bundles share failure domains (an ESO webhook failure blocked MetalLB config reconcile during the 2026-05-14 incident).

**Calico CNI is NOT Flux-managed.**
Calico is installed as a K3s auto-deploy addon: the Tigera operator manifest plus an `Installation` CR templated by Ansible to `/var/lib/rancher/k3s/server/manifests/calico-installation.yaml` (source: `ansible/roles/k3s/templates/calico-installation.yaml.j2`, applied by `ansible/roles/k3s/tasks/calico.yml`, runs only on `k3s_init_node`). The K3s addon controller applies it; the Tigera operator acts on it. To change Calico config, edit the Ansible template and re-run the playbook — `kubectl edit` on the live `Installation` gets reverted by the addon controller.

**DNS: AdGuard Home (not Pi-hole).**
Three LXCs, keepalived VIP at `10.0.10.200`. AGH Sync binary on Saga. Do not suggest Pi-hole.

**Identity: Authentik in asgard K3s.**
OIDC for web apps, LDAP for SSH via SSSD. Local admin accounts on all services as break-glass. Do not suggest Authelia.

**Secrets: two access patterns, three stores.**
- **1Password "Homelab" vault** (humans) — anything a human looks up: web admin passwords, API tokens pasted into configs, LXC root passwords for templates, homelab-hosted DB admin credentials, AppRole credentials for the MacBook control node. Also break-glass user keys and AWS KMS unseal token (already external by design).
- **HashiCorp Vault (asgard K3s, runtime)** — anything a machine pulls at runtime: K8s workload secrets via ESO, Ansible role lookups via AppRole, future runtime-retrieval targets. 3-node Raft HA, AWS KMS auto-unseal, iSCSI storage (Synology CSI, 5Gi PVC, `synology-csi-iscsi-retain`).
- **Ansible Vault (machines, bootstrap)** — secrets needed *before* HashiCorp Vault is reachable: `k3s_token`, RHEL subscription keys, SSH public keys for ansible/break-glass users. Narrow scope by design — only what's required to bootstrap a fresh node up to the point where HashiCorp Vault becomes usable.

The rule: *Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

The bootstrap-vs-runtime split solves the circular dependency: HashiCorp Vault lives in asgard K3s, so anything K3s itself needs to come up cannot live there.

Scope of homelab secret stores: "things that exist because the homelab exists." Personal credentials, external service accounts, and infrastructure under the homelab (bare-metal node root passwords, NAS admin, UCG-Ultra, KPN router) live in 1Password but outside the Homelab vault — they're personal/external, not homelab.

Vault Kubernetes auth method, `eso` policy, `eso` role, AppRole auth method, `ansible` policy, and the `ansible-local`/`ansible-awx` AppRole roles captured in Terraform (`terraform/vault/`) on 2026-05-16. SecretIDs are NEVER in Terraform state — generated manually, stored in 1Password + local env file. See homelab-design.md AppRole bootstrap runbook.

Vault listener is plaintext (`tls_disable = 1`) — deliberate, see Known gotchas.

**Ansible Vault: bootstrap secrets only.**
Holds `k3s_token`, RHEL subscription keys, SSH pubkeys for ansible/breakglass users — the minimum needed to bring a fresh node up to the point HashiCorp Vault is reachable. All other Ansible-pulled secrets live in HashiCorp Vault under `secret/ansible/*`, retrieved via the `community.hashi_vault` lookup with AppRole auth. Proof-of-pattern: `secret/ansible/sftpgo/admin-password` (migrated 2026-05-16).

**Jellyfin: privileged LXC on Urd.**
Intel QuickSync via /dev/dri passthrough. Not in K3s.

**Storage: Synology CSI driver (christian-schlichtherle/synology-csi-chart).**
iSCSI only — NFS creates polluting shared folders on Synology. Single StorageClass: `synology-csi-iscsi-retain` (default). Two instances planned — one per K3s cluster. Not democratic-csi.
Synology CSI creates one iSCSI target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). iSCSI LUNs are single-session by default — after ungraceful node restarts, stale sessions / discovery node records can pin a LUN to the wrong node and block re-attach. Cleanup: `iscsiadm -m session` / `iscsiadm -m node -o delete` on the affected node.
NOTE: there is a vestigial iSCSI target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` left over from an abandoned NFS-CSI attempt — it is NOT in use. The live targets are per-PVC.

**MetalLB: L2 mode.**
Workers have two NICs: eth0 (VLAN 21, K3s traffic) and eth1 (VLAN 20, MetalLB L2 advertisement).
Worker eth1 IPs: 10.0.20.201/202/203 (outside MetalLB pool 10.0.20.11-.99).
L2Advertisement restricted to eth1 interface AND restricted to non-CP nodes via `nodeSelectors` (`matchExpressions` — key `node-role.kubernetes.io/control-plane`, operator `DoesNotExist`). CP nodes have no eth1; without the nodeSelector, MetalLB's L2 election can pick a CP node and announce nowhere.

**Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs/Grafana (jotunheim K3s).**
Zabbix stays as LXC for monitoring independence. VictoriaLogs replaces Loki. VictoriaMetrics replaces Prometheus.

**Ansible: AWX in jotunheim K3s.**
30-minute scheduled reconciliation. Vault-backed credentials.

**VM disks: local LVM-thin.**
Faster than NFS at 1 GbE.

**PBS: privileged LXC on Skuld.**
NFS bind-mounted via Proxmox host.

**Repo: private** (GitHub). Secrets never in Git regardless; SealedSecrets used for bootstrap secrets.

**Internet exposure: KPN Experia Box → UCG-Ultra DMZ.**
KPN configured as "exposed host" / DMZ pointing all unsolicited inbound (IPv4 *and* IPv6) at the UCG-Ultra's WAN IP. UCG-Ultra is the sole firewall policy boundary; KPN does outbound NAT for `192.168.2.0/24` devices (settop box, family devices) only. UCG firewall posture: `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). Port-forwards configured on UCG only. KPN is the one piece of infra that is NOT and will NEVER be in IaC — any change to it lives here in the docs or it doesn't exist.

**LXC management is via Terraform `asgard-lxcs` module.**
Same provider (`bpg/proxmox ~> 0.77`), same `terraform.tfvars` as `asgard-k3s/` (API token + SSH pubkey), parallel structure. To add a new asgard LXC: append to `lxcs.tf`, `terraform apply`, add the host to `inventory/hosts.yml`, write the role + playbook.

**LXC bootstrap flow.**
Day 1:
1. `terraform apply` (LXC has only root SSH, with the key from `var.ssh_public_key`)
2. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml -e 'ansible_user=root' --tags baseline` (smallest root surface)
3. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml` (full deploy as `ansible`, hardening locks root SSH out at the end)

Day N: just step 3. Root SSH is locked out via `AllowUsers ansible recovery`. If something breaks the ansible user, recovery is your way in (key in 1Password).

**Factorio LXC: operator self-service via SFTPGo + reconcile loop.**
LXC 1120 hosts Factorio + SFTPGo on the same host. The operator never gets shell access — they SFTP into `/factorio/` and edit JSON control files. A root-owned Python reconcile script runs as a systemd timer every 30s, reading desired state from `/factorio/control/factorio-control.json` and converging actual state (version installed, service running/stopped, restarts). The operator declares intent; the reconciler owns mechanism. This is the template pattern for future operator-managed game/voice services.

---

## Network

> **MGMT subnet is `10.0.254.0/24`.** Earlier drafts of this doc said `10.0.1.0/24` — that was wrong and nearly caused a correct iSCSI/portal address to be "fixed". VLAN 1 = `10.0.254.0/24`.

```
KPN Experia Box (192.168.2.0/24, untouched) — DMZ → UCG-Ultra WAN
  └── UCG-Ultra WAN
        ├── LAN 1 → Dumb switch (your room)
        │             ├── MacBook dock (VLAN 60, 10.0.60.10 static)
        │             ├── Game PC (VLAN 60)
        │             └── Hue bridge (VLAN 60)
        └── LAN 2 → Dumb switch (spare bedroom)
                      ├── Urd   (10.0.254.11)
                      ├── Verd  (10.0.254.12)
                      ├── Skuld (10.0.254.13)
                      └── Munin (10.0.254.20)
```

### VLANs

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-WRK | Asgard K3s nodes |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-WRK | Jotunheim K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage/NFS (stable) |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### Key IPs
- `10.0.254.1` — UCG-Ultra
- `10.0.254.11/12/13` — Urd/Verd/Skuld
- `10.0.254.20` — Munin (Synology)
- `10.0.10.200` — AdGuard VIP ✅
- `10.0.11.20` — PBS (LXC 1101) ✅
- `10.0.11.201/202/203` — Saga/Mimir/Kvasir ✅
- `10.0.21.11/12/13` — Asgard K3s CP (Göndul/Hlökk/Sigrún)
- `10.0.21.21/22/23` — Asgard K3s workers (Einherjar-urd/verd/skuld) — eth0
- `10.0.20.11–.99` — Asgard K3s MetalLB pool
- `10.0.20.201/202/203` — Worker eth1 IPs (Einherjar-urd/verd/skuld)
- `10.0.31.11/12/13` — Jotunheim K3s CP (Rota/Hildr/Kára)
- `10.0.31.21/22/23` — Jotunheim K3s workers
- `10.0.30.11–.99` — Jotunheim K3s MetalLB pool

### Cluster CIDRs
- Pod CIDR: `10.42.0.0/16` (`k3s_pod_cidr` — used for K3s `cluster-cidr` AND the Calico ipPool)
- Service CIDR: `10.43.0.0/16` (`k3s_service_cidr`)

### Resource IDs
- `1101–1199` — Asgard LXCs (sub-grouped by function)
- `2001–2999` — Asgard K3s VMs
- `3001–3999` — Jotunheim K3s VMs
- `10001+` — Templates

---

## Current build status

- ✅ UCG-Ultra — all VLANs, zones, firewall
- ✅ KPN DMZ → UCG-Ultra (IPv4 + IPv6)
- ✅ Synology (Munin) — factory reset, volumes, NFS, kubernetes user
- ✅ Proxmox cluster — Urd/Verd/Skuld on PVE 9.x, cluster niflheim
- ✅ PBS — LXC 1101 on Skuld, NFS datastore, connected to cluster
- ✅ AdGuard Home — Saga/Mimir/Kvasir, keepalived VIP 10.0.10.200, AGH Sync
- ✅ Asgard K3s — VMs provisioned (Terraform), K3s installed + configured (Ansible — fully IaC), Flux bootstrapped
- ✅ Sealed Secrets — deployed via Flux
- ✅ Synology CSI — iSCSI only, StorageClass synology-csi-iscsi-retain (default)
- ✅ Vault — 3 node Raft HA, AWS KMS auto-unseal, iSCSI storage, initialized; K8s auth method + KV engine + ESO policy/role in Terraform (`terraform/vault/`, imported 2026-05-16)
- ✅ External Secrets Operator — deployed; ClusterSecretStore `vault` Ready
- ✅ MetalLB — L2 working end-to-end. VIP `10.0.20.11` reachable. Required nodeSelectors + Calico/rp_filter fixes (2026-05-14)
- ✅ tigera-operator — fixed 2026-05-15 via MTU explicit (workaround for upstream projectcalico/calico#7851). No longer Degraded.
- ✅ Factorio LXC (1120) — Deployed 2026-05-16. Terraform + Ansible end-to-end. Factorio headless + SFTPGo running. Operator self-service via SFTP on TCP 22022. Game on UDP 34197. Reachable at `factorio.xiiisins.com` (external) and `factorio.niflheim.xiiisins.com` (internal).
- 🔲 Authentik + Redis (in asgard K3s — the next forward step on the K8s side)
- 🔲 Remaining asgard LXCs (Tailscale, Teamspeak, PostgreSQL, HAProxy, Zabbix, Jellyfin)
- 🔲 Jotunheim K3s
- 🔲 Services

---

## Repo structure

```
homelab/
├── CLAUDE.md
├── renovate.json
├── terraform/
│   ├── proxmox/
│   │   ├── asgard-k3s/    # VM definitions (bpg/proxmox provider) — populated
│   │   └── asgard-lxcs/   # LXC definitions — 1120 Factorio; extend per LXC
│   ├── vault/               # Vault config — KV engine, K8s auth, eso policy + role
│   ├── dns/                 # scaffolding (empty)
│   ├── aws/                 # scaffolding (empty)
│   ├── k3s/                 # scaffolding (empty)
├── ansible/
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.yml        # groups: must_run_k3s_cp, must_run_k3s_workers, factorio_host
│   │   └── group_vars/all/  # vars.yml + vault.yml (Ansible Vault encrypted) — adjacent to inventory for auto-discovery
│   ├── playbooks/
│   │   ├── asgard-k3s.yml # roles: baseline → k3s → hardening
│   │   └── factorio-host.yml # roles: baseline → factorio → sftpgo → hardening
│   └── roles/
│       ├── baseline/        # RHEL subscription, qemu-agent, pkg update, break-glass user
│       ├── k3s/             # prereqs, K3s install, Calico CNI addon manifest
│       └── hardening/       # SELinux, SSH, sysctl, module blocklist, banner
│   # NOTE: stray empty ansible/ansible/ dir (mkdir -p slip) — delete it
├── k8s/
│   ├── asgard/
│   │   ├── flux-system/
│   │   │   ├── flux-system/                  # Flux bootstrap manifests
│   │   │   ├── infrastructure.yaml           # Flux Kustomization → infrastructure/
│   │   │   ├── infrastructure-config.yaml    # Flux Kustomization → infrastructure-config/ (ESO config), dependsOn infrastructure
│   │   │   └── metallb-config.yaml           # Flux Kustomization → metallb-config/, dependsOn infrastructure
│   │   ├── infrastructure/
│   │   │   ├── sealed-secrets/
│   │   │   ├── synology-csi/
│   │   │   ├── vault/                        # helmrelease + vault-unseal SealedSecret
│   │   │   ├── external-secrets/
│   │   │   ├── metallb/
│   │   │   ├── authentik/                    # scaffolded, not yet populated
│   │   │   └── kustomization.yaml
│   │   ├── infrastructure-config/
│   │   │   ├── clustersecretstore.yaml       # ESO ClusterSecretStore
│   │   │   └── kustomization.yaml
│   │   ├── metallb-config/
│   │   │   ├── metallb-config.yaml           # IPAddressPool + L2Advertisement
│   │   │   └── kustomization.yaml
│   │   └── apps/
│   └── jotunheim/
│       ├── flux-system/
│       ├── infrastructure/
│       └── apps/
└── docs/
    ├── homelab-design.md
    ├── recovery/
    └── outline/                              # per-topic detail docs 01–22
```

---

## K3s install — how it actually works

K3s itself is fully IaC'd via the Ansible `k3s` role (`tasks/install.yml`, `prerequisites.yml`). It is NOT a source of config drift — the drift concern is the in-cluster *workload* layer and the Calico addon merge behavior, not the K3s install.

- **Version:** `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.33.1+k3s1`), binary downloaded from GitHub releases.
- **Bootstrap order:** `k3s_init_node` (gondul) initialises with `--cluster-init` → other CP nodes join → workers join last. Enforced by `when` conditions + `wait_for` on :6443 + node-count gates.
- **Config templates:** `config-init.yaml.j2` (first CP), `config-server.yaml.j2` (joining CPs), `config-agent.yaml.j2` (workers). CP configs disable `traefik`, `servicelb`, `local-storage`; set `flannel-backend: none` + `disable-network-policy: true` (Calico handles both); set `cluster-cidr`/`service-cidr`, TLS SANs, `selinux: true`. Worker config is minimal: `server` + `token` + `selinux: true`.
- **Token:** generated by K3s on the init node, slurped and distributed as the `k3s_token` fact. (`k3s_token` default in defaults/main.yml is empty — real value in `group_vars/all/vault.yml`.)
- **kubeconfig:** fetched to `~/.kube/niflheim-asgard.yaml`, server address rewritten from `127.0.0.1` to the init node IP.
- **Prerequisites** (`prerequisites.yml`): Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid` enabled, `br_netfilter` + `overlay` modules loaded & persisted, `ip_forward=1`, `bridge-nf-call-iptables`/`ip6tables=1`, swap disabled, `firewalld` disabled.
- **No node taints or labels** are set anywhere in the K3s config — this is why the CP-taint work is a manual addition (see pending tasks).

---

## K3s VM specs

**Control planes (Göndul/Hlökk/Sigrún):**
- 1 vCPU, 2GB RAM, 10GB disk
- Single NIC on VLAN 21
- RHEL 9, K3s, Calico CNI
- Göndul: Urd (temporary, move to Verd next reprovision)
- Hlökk: Verd
- Sigrún: Skuld
- ⚠️ 1 vCPU for etcd-bearing nodes is UNVALIDATED under load. The 2026-05-14 etcd storm makes this a real concern — revisit CP sizing (and possibly etcd disk placement) as part of the Göndul-off-Urd reprovision. (CP VMs were briefly 2vCPU/4GB during IaC iteration; reset to the 1vCPU/2GB baseline — 1GB was found insufficient, hence 2GB.)

**Workers (Einherjar-urd/verd/skuld):**
- 2 vCPU, 4GB RAM, 15GB disk
- eth0: VLAN 21 (K3s traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- RHEL 9, K3s, Calico CNI, iscsiadm
- ⚠️ Multi-homed. The second NIC (eth1/VLAN 20) is a known landmine: it broke Calico autodetection and rp_filter, both of which had to be explicitly configured. See Known gotchas.

---

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- Versions: intended to be pinned, with Renovate managing minor/patch via PR once the homelab reaches a working "2.0" state. The Ansible role versions ARE pinned (`k3s_version`, `calico_version`). HelmRelease charts are currently `version: "0.x"` placeholders — real pinning is a deliberate deferred step gated on the 2.0 state, not an oversight.
- Conventional commits
- Norse mythology naming
- Never commit secrets
- Shell: fish (owner uses fish shell — heredocs `<<EOF` don't work, use pipes or temp files)

---

## Known gotchas

- **etcd on Urd**: The N5095 is too slow for etcd. Never put K3s CP nodes on Urd. On 2026-05-14 an etcd IO storm here cascaded into a multi-hour, multi-symptom outage (overlay, webhook, iSCSI, Vault all affected).
- **Multi-homed workers — Calico autodetection**: Workers have eth0 (VLAN 21) and eth1 (VLAN 20). Calico's default `firstFound` autodetection bound the overlay to eth1 on the workers, breaking cross-node vxlan. Fix (in the Ansible Calico template): `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]`. Do NOT revert to firstFound.
- **Multi-homed workers — rp_filter**: Strict reverse-path filtering (`rp_filter=1`) silently drops MetalLB LoadBalancer traffic arriving on eth1. Hardening role sets `rp_filter=2` (loose mode) on `all` and `default` — required on these multi-homed nodes, still drops genuinely unroutable sources. Do NOT "harden" it back to 1. (Per-interface `eth0`/`eth1` values derive from `all`; setting them explicitly would be belt-and-suspenders.)
- **MetalLB L2 election**: L2Advertisement needs `nodeSelectors` excluding CP nodes (they have no eth1). Without it, election can land on a CP node and announce nowhere.
- **Calico Installation CR merge behavior**: The K3s addon controller MERGES the `Installation` CR rather than replacing it — a removed field can persist in the live object (observed 2026-05-14: a stale `firstFound` survived alongside the new `cidrs`). After changing autodetection, verify with `kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}'` and `kubectl patch ... --type=json` to strip stale fields if needed.
- **tigera-operator `/var/lib/calico/mtu` denied (upstream bug #7851)**: The Tigera operator runs as `container_t` with MCS categories (e.g. `s0:c322,c902`); `/var/lib/calico/mtu` is written by privileged calico-node as `container_var_lib_t:s0` with NO categories. MCS dominance fails on read. The denial is `dontaudit`'d by default policy, so `ausearch` returns nothing until `semodule -DB`. **Fix (shipped 2026-05-15):** set `mtu: 1450` explicitly in the Calico `Installation` CR (`ansible/roles/k3s/templates/calico-installation.yaml.j2`). When MTU is set explicitly the operator never reads the file. This is the upstream-maintainer-recommended workaround. 1450 = 1500 (host) - 50 (VXLAN overhead). Do NOT remove without a replacement fix — the operator will go Degraded again. Revisit if/when upstream actually fixes the operator.
- **Vault SKIP_CHOWN**: Vault Helm chart sets SKIP_CHOWN=true when running as non-root. With iSCSI storage, fsGroup doesn't apply automatically. Fix (in the Vault HelmRelease values): pod-level `securityContext` with `runAsUser: 100`, `runAsGroup: 1000`, `fsGroup: 1000`, `runAsNonRoot: false`; plus an `extraInitContainer` `vault-data-chown` (busybox, `runAsUser: 0`) running `chown -R 100:1000 /vault/data && chmod 750 /vault/data`.
- **Vault TLS disabled — deliberate**: The Vault raft config sets `tls_disable = 1` on the listener; there is no TLS on the listener OR cluster traffic (`cluster_address` is under the same listener). This is a conscious homelab tradeoff (simplicity vs defense-in-depth), not an oversight. Do NOT "fix" it without understanding the cluster-traffic implications. Revisit only as part of deliberate Vault hardening. The `vault-ui` LoadBalancer (VIP `10.0.20.11`) therefore serves plaintext HTTP on :8200 — it is internal/VLAN-only.
- **iSCSI single-session LUNs**: Synology CSI LUNs are single-session. After ungraceful node restarts, a stale session or discovery node record can pin a LUN to a dead/wrong node and block re-attach (`non-retryable iSCSI login failure`, or `iscsi_limit_max_session_count` on the NAS side). Also: the Synology CSI node plugin is a DaemonSet and runs on CP nodes too (no CP taint yet) — a CP node grabbed a worker's LUN during a reschedule. Cleanup: `iscsiadm -m session` / `-m node -o delete` on affected nodes; clear stale sessions NAS-side if needed.
- **Flux CRD timing**: Resources using CRDs installed by a HelmRelease must be in a separate Flux Kustomization with `dependsOn`. Use `infrastructure` for installs and a per-component `<component>-config` Kustomization for CRD-dependent config. Concrete example: ESO installs its CRDs via `installCRDs: true` in the `infrastructure` Kustomization; the ClusterSecretStore lives in `infrastructure-config` which `dependsOn` infrastructure.
- **ESO API version**: ClusterSecretStore uses `external-secrets.io/v1` not `v1beta1`.
- **MetalLB speaker labels**: `app.kubernetes.io/component=speaker` not `component=speaker`.
- **fish shell heredocs**: Don't use `<<EOF` syntax. Use `echo '...' | command` or temp files.
- **Ansible vault loading**: `group_vars/all/` directory with `vars.yml` + `vault.yml`. `k3s_token` is defined in `vault.yml` (default in role is empty).
- **Rancher SELinux repo path**: `prerequisites.yml` pulls `k3s-selinux` from the Rancher `centos/8/noarch` repo even on RHEL 9 nodes. This is the documented Rancher path and the RPM is noarch — works fine, intentional, not a bug.
- **baseline role updates all packages**: The `baseline` role runs `dnf update "*" → latest` + reboot on every run. Re-running the playbook is therefore not pure config-idempotency — it can pull OS updates. Known/intended behavior.
- **bpg/proxmox reboots**: Provider may reboot VMs on apply even without meaningful changes due to IP address drift in state. Expect this — cluster should survive rolling restarts.
- **SELinux dontaudit hides denials**: A "permission denied" with nothing in `ausearch` does NOT mean SELinux is innocent. The default policy `dontaudit`s many denials. Diagnose with `semodule -DB` (disable dontaudit), reproduce, capture AVC, then `semodule -B` to re-enable. Never leave dontaudit disabled — it floods the audit log.
- **Proxmox minimal Debian template is *minimal***: `debian-13-standard_*.tar.zst` lacks `sudo`, `acl`, and other things roles often assume. The `baseline` role installs `sudo` and `acl` OS-agnostically (in `main.yml`, via `ansible.builtin.package` — no-op on RHEL which ships both in `@core`). When writing a new role that uses `become_user: <non-root>`, ensure `acl` is installed via baseline first (Ansible's preferred privilege-drop mechanism uses POSIX ACLs).
- **`ansible.builtin.user` creates accounts with `password: '!'` by default**: PAM's `account` stack interprets this as "account locked" even though pubkey auth via sshd never actually checks a password. Symptom after hardening enables full sshd PAM evaluation: `User <name> not allowed because account is locked` in auth.log; SSH key offered and rejected. Fix: pass `password: '*'` explicitly. `*` is "valid account, no usable password, key auth permitted." Required for any key-auth-only user.
- **Service-specific systemd drop-in dirs (`/etc/systemd/system/<unit>.service.d/`) don't pre-exist**: Roles that drop override.conf files must `file: state: directory` first. SFTPGo bit us; assume any service-specific override needs the dir created.
- **bpg/proxmox API tokens can change `nesting` but no other features**: `keyctl`, `fuse`, and other LXC features require `root@pam` to change once the container exists. Workaround: destroy + recreate. Practical implication: don't put `keyctl: false`/`fuse: false` in the resource block — they're defaults, omitting them avoids future "tried to change keyctl, 403" plans even when values aren't actually changing.
- **Systemd 257 in unprivileged LXC requires `nesting=true`**: Debian 13 ships systemd 257; it uses namespace operations that need CAP_SYS_ADMIN inside the user namespace, which `nesting=true` provides. This flag does NOT enable nested containerization — its name is misleading. The "no nested containerization" rule is preserved by `unprivileged=true`, not by `nesting=false`.
- **`-u root` CLI flag does NOT override `ansible_user` from group_vars**: Group_vars are inventory-level data and outrank CLI `-u` in Ansible's variable precedence. To override during bootstrap, use `-e 'ansible_user=root'` — `-e` is the only level above inventory. Same issue affects the `asgard-k3s` flow if you ever need to re-bootstrap.
- **`group_vars/` is auto-discovered only adjacent to inventory or playbook directories**: `ansible/group_vars/` is invisible to Ansible; must be `ansible/inventory/group_vars/`. If a play seems to be ignoring vault variables, this is usually why.
- **SFTPGo sqlite `data_provider_name` must be an absolute path**: The Debian package unit sets `WorkingDirectory=/etc/sftpgo`, so a relative `sftpgo.db` lands in `/etc/sftpgo/sftpgo.db` — works but FHS-wrong (config dir holding state). Set explicitly to `/var/lib/sftpgo/sftpgo.db` and ensure that dir exists with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in the role**: Background timer firing reconcile before `initial-install.yml` runs causes a race: factorio installs and the service starts (control file defaults to `state=running`) before Ansible can generate the default save → `factorio --create` fails on the .lock file held by the running service. Pattern: enable timer only in `reconcile.yml`, start it explicitly at the end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract**: Factorio writes `.lock` in its install dir at startup. The tarball's extracted ownership won't match. The reconcile script does this; if you ever sidestep reconcile and install manually, you need to chown manually too.

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns. K8s-specific concepts are the knowledge gap. Deep K3s/K8s experimentation is intended for the future jotunheim ("can implode") cluster — asgard is built carefully, not used as a learning sandbox.