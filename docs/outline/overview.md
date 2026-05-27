<!-- docs/outline/overview.md -->

# Homelab — overview & design

A 3-node Proxmox cluster running a small set of reliable services for friends and family, doubling as a senior-level infrastructure portfolio and a Kubernetes learning environment. Everything is defined as code; nothing in production exists outside of Git, Terraform state, or NetBox.

---

## Goals

- **Reliable services for friends and family.** Jellyfin, Teamspeak, Factorio, Immich (planned), Outline. Uptime that doesn't require nursing.
- **Kubernetes as a learning environment.** Real workloads, real failure modes — not a toy cluster.
- **Portfolio at senior/principal infrastructure level.** The repo should read as evidence of how the operator approaches design, not just what they built.

## Scope

**In scope.**

- Self-hosted services for friends and family — media, voice, games, photos, wiki.
- Operator tooling — NetBox, Vault, Outline, observability.
- Learning environment for Kubernetes, GitOps, and modern platform patterns.

**Out of scope.**

- Multi-region or geographically-redundant operation. One physical site.
- Multi-tenancy. One operator, occasional collaborators — not a shared platform.
- Public CI/CD, public build platforms, or externally-facing customer workloads.
- Public-cloud workload hosting. Cloud is used for bootstrap concerns only: AWS KMS for Vault auto-unseal, S3 for Terraform state, Cloudflare for DNS and tunnels.
- Compliance-bearing workloads (HIPAA, PCI, SOC). No regulated data lives here.

## Design principles

- **Everything is defined as code before it exists in production.** Terraform for API-driven resources, Ansible for OS-level state, Flux for in-cluster state. If a knob isn't in code, it doesn't exist.
- **Complexity only where it serves a purpose.** No multi-region, no service mesh, no GitOps-for-GitOps. Every moving part earns its place.
- **Self-healing at every layer.** Patroni for Postgres, keepalived for VIPs, Flux for cluster drift, Semaphore drift-check for fleet drift. Operator notification is a fallback, not the primary mechanism.
- **Full audit trail.** Every change traceable to a human commit or an automated reconciliation. Conventional commits, NetBox as IPAM truth, decisions log for the *why*.

---

## At a glance

| Layer | What |
|---|---|
| Hypervisors | 3× MSI Cubi (i3-1215u, 32 GB) — Urd, Verd, Skuld |
| NAS | Synology DS223J — Munin |
| Orchestration | Two K3s clusters (asgard + jotunheim) — both production |
| GitOps | Flux CD reconciles from this repo |
| Identity | Authentik — OIDC for web apps, LDAP for SSH via SSSD |
| Secrets | HashiCorp Vault (runtime) + 1Password (bootstrap + offline mirror) + Ansible Vault (narrow bootstrap-only) |
| IPAM/DCIM | NetBox — source of truth for devices, VMs, IPs, VLANs |
| DNS | AdGuard Home — three nodes, keepalived VIP |
| Storage | Synology iSCSI (K8s persistent state) + local LVM-thin (VM/LXC disks) |
| Postgres | Patroni cluster (Fulla/Vör/Idunn) behind HAProxy VIP |
| Observability | VictoriaMetrics + VictoriaLogs in-cluster, Zabbix LXC for hosts |
| Ansible orchestration | Semaphore in asgard K3s — NetBox dynamic inventory, drift-check every 6h |

All nodes are 1 GbE. No 2.5 GbE planned.

---

## Topology

```
Internet
  └── KPN Experia Box (DMZ → UCG-Ultra WAN)
        └── UCG-Ultra (sole firewall policy boundary)
              ├── Clients VLAN — MacBook, game PC, Hue bridge
              └── Infra VLANs — 3× Proxmox + NAS
                    ├── Asgard K3s (production-stable)
                    │     ├── CP: Göndul / Hlökk / Sigrún
                    │     └── Workers: Einherjar-urd / verd / skuld
                    ├── Jotunheim K3s (production-experimental, planned)
                    └── LXCs: AdGuard, Postgres, HAProxy, Tailscale, Zabbix, Hermod, Factorio, PBS, ...
```

KPN sits in DMZ mode — every unsolicited inbound packet (IPv4 + IPv6) lands on UCG-Ultra. KPN itself is never in IaC; changes are recorded in docs or they don't exist.

---

## Architecture invariants

These are the load-bearing design choices. Any of them changing is a deliberate re-evaluation, not a drive-by edit.

### Orchestration
- **K3s only.** No Docker Swarm, no plain k8s.
- **Two K3s clusters — asgard and jotunheim — both production.** Separation is failure-domain risk management, not a maturity ladder.
- **GitOps via Flux.** No ArgoCD. No manual `kubectl apply` for production.
- **Calico CNI is a K3s addon, not Flux-managed.** Avoids the CNI bootstrap chicken-and-egg.

### Identity, network, storage
- **Authentik is the only identity provider.** OIDC for web apps, LDAP for SSH via SSSD. Local accounts as break-glass.
- **NetBox is the IPAM/DCIM source of truth.** Git stays the IaC spec; NetBox is the queryable view of reality.
- **AdGuard Home is the DNS resolver.** Three nodes (Saga/Mimir/Kvasir), keepalived VIP. Rewrites are Terraform-managed against the origin; the other two sync from it.
- **UCG-Ultra is the sole firewall policy boundary.** KPN is pass-through. Default posture: Internal → Any allow, External → Internal allow-return, default deny.
- **MetalLB L2 for cluster-internal load balancing.** Workers are multi-homed (eth0 on the node VLAN, eth1 on the MetalLB VLAN).
- **Synology CSI iSCSI only — no NFS for K8s state.** NFS pollutes the Synology namespace with shared folders; iSCSI is single-consumer per PVC and the right shape for stateful workloads.

### Data
- **PostgreSQL only.** No MySQL/Galera. Patroni HA across three nodes, etcd DCS on a separate HAProxy/etcd trio, a single HAProxy VIP for all consumers.
- **VM/LXC disks live on local LVM-thin.** Not NFS — fsync latency over 1 GbE is the wrong shape for write-heavy workloads (PG in particular).

### Observability
- **VictoriaMetrics + VictoriaLogs.** No Prometheus, no Loki, no Grafana. vmui + the native VictoriaLogs UI cover homelab-scale dashboards.
- **Zabbix runs in its own LXC, outside K3s.** Host- and LXC-level monitoring stays in a separate failure domain from the cluster it watches.

### Automation
- **Ansible orchestration via Semaphore in asgard K3s.** NetBox dynamic inventory, per-host-group playbooks, drift-check (`--check --diff`) every 6 hours, alerts to Hermod on diff or failure.
- **Conventional commits, Norse mythology naming.** Primary defines the theme; replicas expand within it (Fulla/Vör/Idunn are Frigg's handmaidens, etc.).

---

## Secrets

Three stores, one rule per consumer class.

**Machines at runtime — HashiCorp Vault.**
K8s workloads read via External Secrets Operator. Ansible on LXCs reads via AppRole. K8s + AppRole auth methods, KV-v2 backend, Raft HA across three nodes with AWS KMS auto-unseal.

**Machines at bootstrap — Ansible Vault (in repo).**
Narrowly scoped to what's needed *before* HashiCorp Vault is reachable: K3s join token, RHEL subscription keys, SSH pubkeys, AWS KMS re-seal copy. Nothing else.

**Humans, homelab-scoped — Vault UI.**
Authentik-gated. Primary access point for looking up any homelab-scoped credential.

**Humans, fallback + bootstrap — 1Password (Homelab vault).**
Two roles. (1) Offline mirror of every homelab Vault secret — discipline rule, manually maintained, periodically audited. (2) Bootstrap credentials that must survive Vault being down: Vault root token, AWS KMS unseal token, SSH recovery key, sealed-secrets master keypair backup, AppRole creds for the MacBook control node.

**Everything else — 1Password (outside the Homelab vault).**
Personal credentials and infrastructure *beneath* the homelab: Proxmox root, Synology admin, UCG-Ultra, KPN, banking, family-shared.

Scope rule: anything that exists *because the homelab exists* lives in the Homelab vault or HashiCorp Vault. Anything beneath the homelab — the hypervisors themselves, the ISP modem, personal accounts — lives in 1Password outside the Homelab vault.

---

## DNS and TLS

Three zones, each with its own wildcard cert (cert-manager DNS-01 against Cloudflare):

- **`xiiisins.com`** — apex, externally resolvable, fronted by Cloudflare. Public-facing entrypoints land here (`wiki.xiiisins.com`, `authentik.xiiisins.com`).
- **`midgard.xiiisins.com`** — internal alias for publicly-reachable services. Same backends as the apex, resolved internally by AdGuard, served by Traefik directly (no Cloudflare tunnel).
- **`niflheim.xiiisins.com`** — internal-only. AdGuard resolves; UCG firewall blocks external. Operator tooling lives here (`vault`, `netbox`, `semaphore`, `logs`, `metric`).

---

## Reliability and change

### Service tiers

- **Critical.** Services whose absence is felt across the household within minutes. DNS (AdGuard VIP), the UCG firewall, the Postgres VIP. Run with active redundancy.
- **Important.** Services whose absence is noticed within hours. Authentik, Jellyfin, Teamspeak, Hermod (Discord notifications), Vault. Single-node failure should be invisible; multi-node failure is acceptable downtime.
- **Best-effort.** Services where a planned or unplanned outage is fine. Factorio, the wiki, observability dashboards, anything experimental in jotunheim. Operator notices and fixes.

### Self-healing posture

- **Postgres.** Patroni runs continuous leader election; HAProxy routes only to the current primary via the `/master` REST check; keepalived floats the HAProxy VIP across the trio. Single-node failure recovers without operator intervention.
- **Cluster state (asgard K3s).** Flux reconciles every manifest from this repo on a continuous loop. Drift in cluster state is corrected automatically.
- **Fleet state (LXCs and VMs).** Semaphore runs an Ansible `--check --diff` drift-check across the fleet every six hours and alerts on diff or failure. Today apply is operator-driven; the direction is auto-remediation in two shapes — small drift (config gone wrong) converged automatically by re-applying the IaC, and larger failures (a VM that crashes, hangs, or otherwise misbehaves) handled by tearing the VM down and rebuilding it from scratch with no manual steps in the loop. Same posture as Patroni and Flux, extended to the fleet layer.
- **DNS.** Three AdGuard nodes with a keepalived VIP and one-minute sync from the origin. Single-node loss is invisible.
- **Observability.** Zabbix sits in its own LXC, outside K3s. If K3s breaks, Zabbix still notifies.

### Change model

Three named paths. Nothing legitimate happens outside them.

1. **Git push.** A Terraform plan/apply, an Ansible playbook run, or a Flux reconcile picks up a commit to this repo. Reviewed before merge; nothing lands in production by accident.
2. **Drift-check + apply.** Semaphore runs an Ansible drift-check across the fleet every six hours and notifies on diff or failure. Today the apply is operator-driven — the operator inspects the diff, then either updates the IaC to match reality or runs the playbook to converge reality to IaC. The direction is auto-convergence: Semaphore applies the IaC automatically on detected drift, with the operator notified after the fact. The spec stays in the repo; reality converges to it.
3. **Break-glass.** Direct edits on a host are explicitly out-of-band. They're logged, and the IaC must be reconciled the same day. The `recovery` user exists for this and only this.

---

## Phase posture

The build is sequenced as numbered phases (1 through 8) tracked in the build sequence doc. Asgard K3s and its core services are live; Jotunheim K3s is planned for Phase 7. The current focus is the 1.0 stabilization waves — validation, recovery hardening, role debt, observability depth — before laying further workloads on top.

---

## Where to go next

Three reader paths.

**I'm on call — something's wrong.**
Start with **Troubleshooting** for common symptoms and the first three things to check. Move to the relevant **Service** page for deeper context. Reach for **Procedures** if a rebuild is on the table.

**I'm new to the homelab.**
You're in the right place. From here: **Components & interactions** for how the pieces wire together, then **Hardware** for the physical layer. **Services** is the catalog when you're ready to dig into any one of them.

**I want a URL or a login.**
Open the **URLs** page. **Services** has a one-paragraph summary of what each one is for if you're not sure which to pick.
