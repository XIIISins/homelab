<!-- docs/outline/components-and-interactions.md -->

# Components & interactions

The homelab is layered. Each layer has a small set of components with a clear role, and the interesting behaviour is in how layers talk to each other. This page is the map: what exists, where it runs, and the handful of runtime flows worth carrying in your head. The subpages go deeper on each layer.

---

## The layers

From physical hardware up to the things people click on:

1. **Physical.** Three identical MSI Cubi nodes (Urd, Verd, Skuld), the Synology NAS (Munin), the UCG-Ultra router/firewall and two unmanaged switches. See **Hardware**.
2. **Network.** The UCG-Ultra routes the VLANs and is the sole firewall boundary. AdGuard Home (three nodes behind a keepalived VIP) is the resolver. MetalLB gives Kubernetes services addresses on a dedicated VLAN. Everything above depends on this layer.
3. **Compute.** The Proxmox cluster `niflheim`. VMs host the K3s nodes and the control node; LXCs host services that deliberately sit outside Kubernetes.
4. **Storage & data.** Local LVM-thin for guest disks, a tiered model for Kubernetes volumes (iSCSI, NFS, `local-path`, emptyDir), PostgreSQL under Patroni, Garage for S3, PBS and S3 for backups.
5. **Identity & secrets.** Authentik for sign-in, HashiCorp Vault for runtime secrets, 1Password for bootstrap credentials and the offline mirror, Ansible Vault for the narrow set needed before Vault exists.
6. **Orchestration.** Terraform provisions, Ansible configures, Flux reconciles the cluster, Semaphore schedules and checks for drift, and an AIOps tier adds approval-gated automation.
7. **Edge.** Traefik fronts every web name; cert-manager issues certificates for three DNS zones; Cloudflared tunnels public hostnames in.
8. **Observability.** VictoriaMetrics and VictoriaLogs inside the cluster, Zabbix and Hermod outside it, a prober for active checks.

Lower layers do not know about upper ones. Upper layers need everything beneath them healthy, which is why the services that K3s itself depends on (DNS, Postgres, Zabbix, Hermod, PBS) run outside K3s.

---

## Components at a glance

| Component | Role | Runs on |
|---|---|---|
| Proxmox VE | Hypervisor, cluster, LVM-thin guest storage | Urd, Verd, Skuld |
| Synology DSM | NAS: NFS, one iSCSI LUN class, media, backup datastore | Munin |
| UCG-Ultra | Router, firewall, VLANs | Edge of the network |
| AdGuard Home | DNS resolver and rewrites | LXCs Saga, Mimir, Kvasir |
| K3s (asgard) | The production Kubernetes cluster | Three control planes, three workers (VMs) |
| Calico | CNI, installed as K3s addons | asgard |
| MetalLB | L2 service addresses | asgard workers |
| Traefik | Ingress via Gateway API | asgard |
| Cloudflared | Outbound tunnel for public hostnames | asgard |
| cert-manager, trust-manager | Certificates and CA distribution | asgard |
| Flux CD | GitOps reconciler | asgard |
| Authentik | OIDC, SAML and ForwardAuth sign-in | asgard |
| HashiCorp Vault | Runtime secrets, Raft HA, KMS auto-unseal | asgard |
| External Secrets Operator | Vault to Kubernetes Secret sync | asgard |
| PostgreSQL + Patroni | The relational database cluster | LXCs Fulla, Vör, Idunn |
| HAProxy + keepalived + etcd | Postgres VIP and Patroni's consensus store | LXCs Hlin, Eir, Snotra |
| Garage | Single-node S3 store | asgard |
| Synology CSI, csi-driver-nfs, local-path | The storage tiers for Kubernetes | asgard |
| NetBox | IPAM/DCIM and the Ansible inventory source | asgard |
| Semaphore | Ansible scheduler and drift-check | asgard |
| VictoriaMetrics, VictoriaLogs | Metrics and logs, with vmagent and vlagent shippers | asgard (stores); DaemonSet and systemd (shippers) |
| Zabbix | Host and LXC monitoring | LXC Hugin |
| Hermod | Notification hub to Discord | LXC |
| PBS | Backups of VMs and LXCs | LXC on Urd |
| Tailscale | Remote access: two subnet routers, one exit node | LXCs, plus Munin |
| Frigg | Control node: Terraform/Ansible/Flux cycle, AIOps Toolbelt | VM |
| Gná, Ratatoskr | AIOps diagnosis agent; approval bot | LXCs |
| Jellyfin | Media server with QuickSync | LXC on Urd |
| Terraform, Ansible | Provisioning and configuration | Frigg, operator workstation, Semaphore |

---

## Canonical flows

### 1. A public request

```
Browser → Cloudflare (edge cert) → tunnel → cloudflared pod
        → Traefik → HTTPRoute → app pod → Postgres VIP
```

Detail on **Edge**. Public names exist only for a few services; the rest never leave the LAN.

### 2. An internal request

```
Client → AdGuard VIP (rewrite) → Traefik VIP 10.0.20.10 → HTTPRoute → app
```

For a publicly-reachable service, the `midgard` alias skips Cloudflare. For a tool with no SSO, Traefik asks Authentik first (ForwardAuth). See **Network** and **Edge**.

### 3. A secret reaching a pod

```
Vault (secret/k8s/<app>) → External Secrets (Kubernetes auth) → Kubernetes Secret → pod
```

Terraform-minted secrets are written straight to the consumer's Vault path. See **Identity & secrets**.

### 4. A secret reaching Ansible

```
Playbook → community.hashi_vault lookup → AppRole login → secret/ansible/…
```

Only what is needed before Vault exists comes from Ansible Vault instead.

### 5. A change landing

```
Branch → PR → CI gate → review → merge to main
   ├─ k8s/…       → Flux reconciles (this is the deploy)
   ├─ terraform/… → apply from the main checkout
   └─ ansible/…   → run by hand or by Semaphore
```

See **GitOps & automation**.

### 6. A new guest

```
Terraform (guest) + NetBox declarations → Ansible baseline as root
    → full play as ansible → Semaphore drift-checks it every 6 h
```

NetBox then feeds the inventory the next run uses. See **Compute & hypervisors**.

### 7. A database failover

```
Leader fails → Patroni elects a new one (etcd on the HAProxy trio)
    → HAProxy's /master check follows → VIP serves the new leader
    → Patroni callback posts to Hermod → Discord
```

Typically 10 to 30 seconds. Applications recover through connection retry. See **Storage & data**.

### 8. An alert

```
Zabbix trigger ─┬→ Hermod → Discord channel by tag
                └→ diagnosis agent → Toolbelt queries → threaded diagnosis in Discord
Actions need an approval in Discord (Ratatoskr) before Semaphore runs them.
```

See **Observability** and **GitOps & automation**.

---

## Subpages

| Page | Covers |
|---|---|
| **Compute & hypervisors** | VMs vs LXCs vs pods, ID scheme, specs, bootstrap flow |
| **Network** | VLANs, DNS, MetalLB, the multi-homed worker rules |
| **Storage & data** | Storage tiers, Postgres, Garage, backups |
| **Identity & secrets** | Authentik, the secret stores, Vault, AppRole |
| **GitOps & automation** | Tool ownership, Flux layering, Semaphore, AIOps |
| **Edge** | Gateways, certificates, tunnel, access paths |
| **Observability** | Metrics, logs, Zabbix, Hermod |

---

## See also

- **Hardware**: the physical layer in depth.
- **Services and purpose**: what each user-facing service is for.
- **Procedures**: runbooks for rebuilds, upgrades and rotations.
- **Troubleshooting**: symptom-first diagnosis by area.
