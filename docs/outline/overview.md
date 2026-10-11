<!-- docs/outline/overview.md -->

# Homelab — overview & design

A three-node Proxmox cluster running a small set of reliable services for friends and family, doubling as a Kubernetes learning environment and a senior-level infrastructure portfolio. Everything is defined as code; nothing in production exists outside Git, Terraform state or NetBox.

---

## Goals

- **Reliable services for friends and family.** Jellyfin, Teamspeak, Factorio, Immich, a wiki. Uptime that does not need nursing.
- **Kubernetes as a learning environment.** Real workloads and real failure modes, not a toy. Deep experiments go on an ephemeral cloud cluster, not on the production one.
- **A portfolio at senior and principal infrastructure level.** The repository should read as evidence of how the operator designs, not just what was built.

## Scope

**In scope.** Self-hosted services for friends and family; operator tooling (NetBox, Vault, observability, automation); a platform for learning Kubernetes, GitOps and modern operations.

**Out of scope.** Multi-region operation (one physical site); multi-tenancy; public CI or customer-facing workloads; public-cloud workload hosting (cloud is used for bootstrap and edge: AWS KMS and S3, Cloudflare, a DigitalOcean offsite node and burst cluster); regulated data.

## Design principles

- **Everything is defined as code before it exists in production.** Terraform for anything with an API, Ansible for operating systems, Flux for the cluster. If a knob is not in code, it does not exist.
- **Complexity only where it serves a purpose.** No multi-region, no service mesh. Every moving part earns its place; features are dropped when the hardware cannot carry them.
- **Self-healing at every layer.** Patroni for Postgres, keepalived for VIPs, Flux for the cluster, a drift-check for the fleet. Telling the operator is the fallback, not the mechanism.
- **A full audit trail.** Every change traces to a human commit or an automated reconciliation.

---

## At a glance

| Layer | What |
|---|---|
| Hypervisors | 3× MSI Cubi (i3-1215U, 32 GB): Urd, Verd, Skuld |
| NAS | Synology DS223J: Munin |
| Orchestration | One K3s cluster, asgard: three control planes, three workers |
| GitOps | Flux CD reconciles from this repository |
| Identity | Authentik: OIDC, SAML and ForwardAuth |
| Secrets | HashiCorp Vault (runtime), 1Password (bootstrap and offline mirror), Ansible Vault (narrow bootstrap) |
| IPAM/DCIM | NetBox: devices, VMs, IPs, VLANs, and the Ansible inventory source |
| DNS | AdGuard Home on three LXCs behind a keepalived VIP |
| Storage | Tiered: local LVM-thin, `local-path`, NFS, a little iSCSI, emptyDir |
| Database | PostgreSQL under Patroni, behind a HAProxy VIP |
| Observability | VictoriaMetrics and VictoriaLogs in-cluster; Zabbix and Hermod outside it |
| Automation | Semaphore (drift-check every 6 h), plus an approval-gated AIOps tier |

All links are 1 GbE; no 2.5 GbE is planned.

---

## Topology

```
Internet
  └── KPN Experia Box (DMZ → UCG-Ultra WAN)
        └── UCG-Ultra (the only firewall policy boundary)
              ├── Clients VLAN: MacBook, game PC, Hue bridge
              └── Infrastructure VLANs: 3× Proxmox + NAS
                    ├── Asgard K3s
                    │     ├── Control planes: Göndul / Hlökk / Sigrún
                    │     └── Workers: Einherjar-urd / verd / skuld
                    └── LXCs and VMs: AdGuard, Postgres, HAProxy, Tailscale,
                        Zabbix, Hermod, Factorio, PBS, Jellyfin, AIOps hosts, Frigg
```

KPN is in DMZ mode: every unsolicited inbound packet lands on the UCG-Ultra. KPN itself is never in IaC, so its configuration is recorded in the docs.

A second Kubernetes cluster (Jotunheim) was designed and dropped for lack of RAM; its network and ID ranges stay reserved.

---

## Architecture invariants

The load-bearing choices. Changing one is a deliberate re-evaluation, not a drive-by edit.

### Orchestration
- **K3s only.** One production cluster.
- **GitOps via Flux.** No ArgoCD, and no manual `kubectl apply` against production.
- **Calico is a K3s addon, not Flux-managed**, installed and upgraded by Ansible playbooks. It avoids a CNI bootstrap loop, and K3s pruning per addon file makes hand edits dangerous.

### Identity, network, storage
- **Authentik is the only identity provider**, with local break-glass accounts.
- **NetBox is the IPAM source of truth.** Git is the spec; NetBox is the queryable view.
- **AdGuard Home is the resolver**, with Terraform-managed rewrites.
- **The UCG-Ultra is the sole firewall boundary.** KPN is pass-through.
- **MetalLB runs in L2 mode.** Workers are multi-homed and a set of routing settings must never be "hardened" away.
- **Storage is tiered by access pattern** because the NAS allows only about ten iSCSI LUNs: block for the few memory-mapped stores, NFS for file-class data, node-local disks for replicated state, emptyDir for caches.

### Data
- **PostgreSQL only**, with Patroni HA, etcd on a separate HAProxy trio and a single VIP for all consumers.
- **VM and LXC disks live on local LVM-thin**, never NFS.

### Observability and automation
- **VictoriaMetrics and VictoriaLogs; no Prometheus, Loki or Grafana.**
- **Zabbix sits outside K3s**, in its own failure domain.
- **Semaphore schedules Ansible** against a NetBox dynamic inventory.
- **Conventional commits and Norse naming**: the primary defines the theme, replicas expand within it.

---

## Secrets

Three stores, one rule per consumer.

- **Machines at runtime: HashiCorp Vault.** Workloads read through External Secrets; Ansible through AppRole. Raft HA with AWS KMS auto-unseal.
- **Machines at bootstrap: Ansible Vault.** Only what is needed before Vault exists.
- **Humans, homelab-scoped: the Vault UI**, behind Authentik.
- **Humans, bootstrap and everything else: 1Password.** The Homelab vault holds credentials that must survive Vault being down plus an offline mirror of every Vault secret. Credentials for infrastructure beneath the homelab (Proxmox, Synology, the router, the ISP) and personal ones live outside it.

Scope rule: what exists *because the homelab exists* is in Vault or the Homelab vault; what sits beneath or beside it is in 1Password.

---

## DNS and TLS

Three zones, each with a wildcard certificate (cert-manager DNS-01 against Cloudflare):

- **`xiiisins.com`**: public, fronted by Cloudflare.
- **`midgard.xiiisins.com`**: internal alias of public services.
- **`niflheim.xiiisins.com`**: internal-only, where operator tooling lives.

---

## Reliability and change

### Service tiers

- **Critical.** DNS, the firewall and the Postgres VIP. Active redundancy.
- **Important.** Authentik, Jellyfin, Teamspeak, Hermod, Vault. One node failure is invisible.
- **Best-effort.** Factorio, the wiki, observability UIs, anything experimental.

### Self-healing posture

- **Postgres.** Patroni elects a leader, HAProxy follows via a REST check, keepalived floats the VIP. Failover takes 10–30 seconds.
- **The cluster.** Flux reconciles continuously.
- **The fleet.** Semaphore runs a check-and-diff of every LXC and VM every six hours and alerts on drift. The direction is auto-convergence of small drift and rebuild-from-scratch for larger failures.
- **DNS.** Three AdGuard nodes with a VIP and one-minute sync.
- **Monitoring.** Zabbix and Hermod are outside K3s, so a cluster outage still pages.
- **Automation with a human in the loop.** An approval-gated AIOps tier diagnoses alerts and, with the operator's approval, acts on them; autonomous action is confined to disposable canaries.

### Change model

1. **Git push.** A branch and pull request pass a static CI gate and a diff review; merging to `main` is the Kubernetes deploy, and Terraform and Ansible are applied deliberately.
2. **Drift-check and apply.** The scheduled check tells the operator when reality differs from Git; the IaC is then corrected or applied.
3. **Break-glass.** Direct edits on a host are out-of-band, logged, and reconciled into the IaC the same day.

---

## Current posture

The platform is built: the cluster, identity, secrets, storage, observability and the application set are live. The media stack (Jellyfin and its automation) is in acceptance. The remaining work is mostly hardening the control node and maturing the AIOps tiers (autonomous healing, a fleet rebuild loop, agent-authored pull requests and rightsizing). A vm-operator migration and a second cluster are deferred or dropped.

---

## Where to go next

**I'm on call and something is wrong.** Start with **Troubleshooting** for symptoms and first checks, then the relevant **Services and purpose** page, and **Procedures** if a rebuild is on the table.

**I'm new to the homelab.** Read **Components & interactions** for how the pieces wire together, then **Hardware** for the physical layer. **Services and purpose** is the catalog.

**I want a URL or a login.** Open **URLs**.
