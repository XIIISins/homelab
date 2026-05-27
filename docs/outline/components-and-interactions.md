<!-- docs/outline/components-and-interactions.md -->

# Components & interactions

The homelab is layered. Each layer has a small set of components with a clear role; interactions happen across layers in named patterns. This page is the map — what's where, how the pieces connect, and the half-dozen runtime flows worth carrying in your head. Subpages go deeper on each component class.

---

## The layers

From physical hardware up to the things humans click on:

1. **Physical.** Three identical MSI Cubi nodes (Urd / Verd / Skuld), the Synology NAS (Munin), the UCG-Ultra router/firewall, two dumb switches.
2. **Network.** UCG-Ultra carries 10 VLANs and is the sole firewall policy boundary. AdGuard Home (three-node keepalived VIP) resolves DNS. MetalLB announces service VIPs in-cluster on a dedicated VLAN. Everything above depends on this layer being healthy.
3. **Compute.** Proxmox `niflheim` cluster on the three nodes. VMs host the K3s control planes and workers; LXCs host services that deliberately don't belong in Kubernetes.
4. **Storage & data.** PostgreSQL (Patroni-managed) for relational data. Synology iSCSI for K8s persistent volumes. Garage for S3 object storage. Local LVM-thin for VM/LXC disks.
5. **Identity & secrets.** Authentik issues OIDC for web apps and LDAP for SSH. HashiCorp Vault holds machine secrets. 1Password holds bootstrap-only credentials plus an offline mirror of everything in Vault.
6. **Orchestration.** Flux CD reconciles cluster state from this repo. Semaphore reconciles fleet state (LXCs + VMs) via Ansible. Terraform provisions everything beneath.
7. **Edge.** Traefik fronts every internal hostname. Cloudflared tunnels publicly-facing hostnames out to Cloudflare. cert-manager issues wildcard TLS for the three DNS zones.
8. **Observability.** VictoriaMetrics + VictoriaLogs collect metrics and logs from inside the cluster. Zabbix watches hosts and LXCs from its own LXC (separate failure domain). Hermod fans alerts out to Discord.

Lower layers don't know about upper layers. Upper layers depend on every layer beneath them being healthy.

---

## Components at a glance

| Component | Role | Home |
|---|---|---|
| Proxmox VE | Hypervisor, cluster, LVM-thin storage | All three nodes |
| Synology DSM | NAS, iSCSI target, NFS server | Munin |
| UCG-Ultra | Router, firewall, VLAN routing | Edge |
| AdGuard Home | DNS resolver and rewrite engine | Saga / Mimir / Kvasir (LXC trio) |
| K3s (asgard) | Production-stable Kubernetes | Göndul / Hlökk / Sigrún CPs + three workers |
| K3s (jotunheim) | Production-experimental Kubernetes (planned) | Rota / Hildr / Kára CPs + three workers |
| Flux CD | GitOps reconciler | asgard K3s |
| Calico | CNI overlay | both K3s clusters |
| MetalLB | L2 load balancer (VIP announcement) | both K3s clusters |
| Traefik | Ingress + Gateway API implementation | asgard K3s |
| Cloudflared | Outbound tunnel for public hostnames | asgard K3s |
| cert-manager | TLS cert issuance (DNS-01 against Cloudflare) | asgard K3s |
| Authentik | OIDC, LDAP, identity | asgard K3s |
| HashiCorp Vault | Machine secrets, KV-v2, Raft HA, KMS auto-unseal | asgard K3s |
| External Secrets Operator | Vault → K8s Secret materialization | asgard K3s |
| PostgreSQL (Patroni) | Relational DB cluster | Fulla / Vör / Idunn (LXC trio) |
| HAProxy + keepalived | PG VIP routing + failover | Hlin / Eir / Snotra (LXC trio) |
| Synology CSI | iSCSI dynamic provisioning | both K3s clusters |
| Garage | S3-compatible object store | asgard K3s |
| NetBox | IPAM / DCIM source of truth | asgard K3s |
| Semaphore | Ansible scheduler, drift-check | asgard K3s |
| VictoriaMetrics + VictoriaLogs | Metrics + logs ingest, in-cluster | asgard K3s |
| vmagent + vlagent | Metric/log shippers | DaemonSet (K3s nodes) + systemd (LXCs/VMs) |
| Zabbix | Host- and LXC-level monitoring | Hugin (LXC) |
| Hermod | Notification aggregator (AppriseAPI + Caddy) | Hermod (LXC) |
| Terraform | IaC provisioner | Operator workstation |
| Ansible | Configuration management | Semaphore + operator workstation |

---

## Canonical interaction flows

The flows worth knowing by feel. Everything else is a variation on one of these.

### Public service request

A friend opens `wiki.xiiisins.com`:

1. Browser resolves the hostname via Cloudflare → Cloudflare edge.
2. Cloudflare proxies through the outbound tunnel to **Cloudflared** running in asgard K3s.
3. Cloudflared targets **Traefik's ClusterIP** with the original Host header preserved. Targeting the MetalLB VIP from inside the cluster would trombone the traffic and break; ClusterIP DNS is the right shape.
4. Traefik routes by hostname to the Outline pod's Service.
5. Outline reads/writes sessions against its companion Redis pod, persists pages and attachments to **Garage** (S3), persists metadata to **Postgres** via the Patroni HAProxy VIP at `10.0.10.210`.
6. HAProxy steers the Postgres connection to whichever Patroni node is currently leader (REST-API health check on `/master`).

### Internal service request

The operator opens `netbox.niflheim.xiiisins.com`:

1. Browser asks AdGuard (the LAN-configured resolver via the keepalived VIP at `10.0.10.200`).
2. AdGuard returns the **Traefik MetalLB VIP** (`10.0.20.10`).
3. Browser hits Traefik directly. No Cloudflared in the path — internal-only zone.
4. Traefik routes to the NetBox pod. NetBox completes OIDC against Authentik (also internal-resolved), uses Postgres via the same Patroni VIP, serves the page.

### Secret retrieval — K8s workload

An app pod starts and needs a database password:

1. The app's `ExternalSecret` CR (declared in this repo) names a Vault path and a target K8s Secret.
2. **External Secrets Operator** authenticates to Vault using the cluster's `ClusterSecretStore` (Vault's Kubernetes auth method binds the pod's ServiceAccount to a Vault policy).
3. ESO reads the Vault KV path and materializes the K8s Secret in the app's namespace.
4. The app pod mounts the K8s Secret as `envFrom` (or as a file) — no direct Vault knowledge.

### Secret retrieval — Ansible on an LXC

An Ansible role on a Postgres host needs a replication password at run time:

1. The role calls `community.hashi_vault.vault_kv2_get` with the relevant AppRole credentials (`ansible-local` for the operator's workstation, `ansible-awx` for Semaphore).
2. Vault validates the RoleID + SecretID pair and issues a short-lived token.
3. The role reads the KV path with that token, uses the value, discards the token.
4. The AppRole RoleID + SecretID pair is the only persistent credential; SecretID rotation is operator-driven via the `rotate-approle` / `rotate-semaphore-approle` helpers.

### Change deployment — cluster (Flux path)

A K8s manifest change ships to production:

1. Operator commits to this repo and pushes to GitHub.
2. **Flux** (in asgard K3s) polls the repo, sees the change, processes its Kustomizations in dependency order.
3. Any HelmRelease whose values changed reconciles its chart; raw manifests apply directly.
4. Pods roll. Observability picks up the change via the usual log/metric path.

### Change deployment — fleet (Semaphore path)

An Ansible role change ships to LXCs and VMs:

1. Operator commits and pushes.
2. **Semaphore** picks up the change on its next scheduled run (drift-check every six hours, or the operator triggers manually).
3. Drift-check runs `--check --diff` against the fleet; any diff is reported to Hermod with a compact per-host summary.
4. Apply is operator-driven today. The direction is auto-apply on detected drift (see the [Homelab overview](./overview.md) for the reliability posture).

### VM / LXC creation

A new LXC lands:

1. Operator declares the LXC in `terraform/proxmox/asgard-lxcs/` and applies. Terraform calls the Proxmox API (via `bpg/proxmox`) to clone the template, seed cloud-init, and assign the static IP.
2. Operator declares the matching NetBox VM + interface + IP in `terraform/netbox/vms.tf` and applies — NetBox stays the IPAM truth.
3. Operator runs the appropriate Ansible playbook (`asgard-<group>.yml`) to bootstrap baseline + role-specific configuration.
4. The next inventory refresh picks up the new NetBox record; Semaphore's drift-check includes the host automatically from then on.

### Failure → notification

A Postgres node becomes leader after a failover:

1. Patroni detects the local state change on the new leader.
2. Patroni invokes its `on_role_change` callback — a small shell script that hits Hermod's `/notify/<config-key>` endpoint with a `patroni` tag.
3. Hermod's AppriseAPI maps the tag to a Discord channel (Hrist for critical, Mist for alerts, Ölrún for media, Hel as quarantine for untagged).
4. Operator sees the message in Discord within seconds.

Zabbix triggers and Patroni quorum-loss detection feed the same pipeline — different sources, identical fan-out.

---

## Where to go deeper

Each of these is a subpage of this section.

- **Network** — VLAN table, IP assignments, firewall posture, DNS zones.
- **Compute & hypervisors** — Proxmox cluster, VM/LXC topology, resource ID scheme.
- **Storage & data** — Synology iSCSI, LVM-thin, Garage S3, Postgres + Patroni topology.
- **Identity & secrets** — Authentik, Vault, 1Password, three-store architecture.
- **GitOps & automation** — Flux structure, Semaphore templates, Ansible role layout, drift-check.
- **Edge** — Traefik, Cloudflared, cert-manager, the three DNS zones.
- **Observability** — VictoriaMetrics + VictoriaLogs, Zabbix, Hermod's tag taxonomy.
