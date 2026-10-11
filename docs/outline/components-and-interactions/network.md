<!-- docs/outline/components-and-interactions/network.md -->

# Network

How packets get around the homelab: the VLAN model, who answers DNS, how Kubernetes services get reachable addresses, and the handful of routing details that make multi-homed Kubernetes workers behave.

---

## VLAN model

The UCG-Ultra routes all VLANs and is the only firewall boundary (see **Networking** under Hardware). The VLANs separate by role, not by trust: they all sit in the Internal zone.

| VLAN | Subnet | Purpose |
|---|---|---|
| 1 | `10.0.254.0/24` | Management: Proxmox nodes, Munin, the UCG-Ultra |
| 10 | `10.0.10.0/24` | Asgard VIPs (keepalived): the AdGuard and Postgres VIPs |
| 11 | `10.0.11.0/24` | Asgard LXCs and the control-node VM |
| 20 | `10.0.20.0/24` | K3s service VIPs (MetalLB pool) and worker eth1 |
| 21 | `10.0.21.0/24` | K3s nodes (control planes and worker eth0) |
| 30 / 31 | `10.0.30.0/24`, `10.0.31.0/24` | Reserved, unused (the dropped second cluster) |
| 60 | `10.0.60.0/24` | Personal devices |
| 100 | `10.0.100.0/24` | Storage / NFS |
| 222 | `10.0.222.0/24` | Untrusted / quarantine |

The management subnet is `10.0.254.0/24`, not `10.0.1.0/24`; an old draft recorded it wrongly and the live network has always been `.254`.

### Key addresses

| Address | What |
|---|---|
| `10.0.10.200` | AdGuard VIP: the only DNS address clients are given |
| `10.0.10.210` | Postgres VIP (HAProxy) |
| `10.0.20.10` | Traefik: every K8s-fronted web name resolves here |
| `10.0.20.11` / `.12` | Vault UI / Teamspeak (pinned MetalLB addresses) |
| `10.0.21.11–.13` | K3s control planes |
| `10.0.21.21–.23` | K3s workers (eth0); eth1 is `10.0.20.201–.203` |
| `10.0.11.x` | LXCs, e.g. PBS `.20`, Hugin `.21`, Hermod `.22`, Factorio `.220`, Jellyfin `.223`, Postgres `.230–.232`, HAProxy `.233–.235`, Frigg `.30` |

Cluster-internal ranges: pods `10.42.0.0/16`, services `10.43.0.0/16`.

---

## DNS

**AdGuard Home** runs on three LXCs (Saga, Mimir, Kvasir), one per node. A keepalived VIP (`10.0.10.200`) floats across them; clients and DHCP only ever know the VIP. Saga is the origin: `adguardhome-sync` pushes its configuration to the replicas every minute. The resolver does not forward to a public fallback resolver, because public resolvers return NXDOMAIN for the internal zones and clients cache that as truth.

**Three zones**, each with a clear job:

| Zone | Served by | For |
|---|---|---|
| `xiiisins.com` | Cloudflare (public) | Services reachable from the internet |
| `midgard.xiiisins.com` | AdGuard (internal) | Internal alias of those same services, so LAN clients skip the Cloudflare tunnel |
| `niflheim.xiiisins.com` | AdGuard (internal) | Internal-only services and every host |

**Rewrites are Terraform-managed.** A name is added to `locals.rewrites` in `terraform/adguard/rewrites.tf`; the provider writes to Saga and sync fans it out. Hand-editing in the AdGuard UI is outside the spec and is overwritten. A one-step end-to-end check exists: `curl https://smoketest.niflheim.xiiisins.com/anything` returns "smoketest ok" if the rewrite, Traefik routing and a backend all work.

**In-cluster split horizon.** Pods resolve through CoreDNS. For internal FQDNs that Traefik serves, a `coredns-custom` rewrite points the name at Traefik's in-cluster service instead of the MetalLB VIP. Reason: in MetalLB's L2 mode, a pod dialling a VIP on the same VLAN as its node hairpins badly, so pods are routed to the cluster-internal path.

**Tailnet clients** get MagicDNS plus split DNS for `niflheim` and `midgard` to the AdGuard VIP over the Tailscale subnet routers; the public apex is deliberately not split.

---

## MetalLB and service addresses

Kubernetes `LoadBalancer` services get addresses from MetalLB in **L2 mode**: one worker answers ARP for the VIP and traffic flows to it. The pool is `10.0.20.10–.99`, and every service pins its address (Traefik `.10`, Vault UI `.11`, Teamspeak `.12`) so nothing races for the bottom of the pool.

Two consequences worth remembering:

- **A VIP does not answer ping.** L2 mode only ARPs; ICMP to the VIP finds no kube-proxy rule and returns host-unreachable. Test with TCP.
- **Host processes on a K3s node cannot dial a MetalLB VIP from the same cluster.** Anything running outside a pod on a node (like a host-installed log shipper) uses a companion ClusterIP instead.

---

## The multi-homed worker rules

Workers have two NICs because MetalLB advertises on VLAN 20 while the nodes live on VLAN 21. That makes the workers the only multi-homed guests, and four settings, all laid down by the `k3s` Ansible role, keep it working. They look like things to harden; they must not be hardened away.

| Setting | Why it exists |
|---|---|
| Calico autodetection pinned to `10.0.21.0/24` | The default "first found" binds the overlay to eth1 and breaks cross-node traffic. |
| `rp_filter = 2` (loose) | Strict reverse-path filtering silently drops LoadBalancer traffic arriving on eth1. |
| `route_localnet = 1` | MetalLB only ARPs the VIP; without this the kernel drops packets to the unbound address. |
| VLAN 20 source-based policy routing | Replies from a VIP must leave via eth1; otherwise the reply is asymmetric and the UCG-Ultra's stateful firewall drops it. |

The MetalLB `L2Advertisement` is restricted to workers, since control planes have no eth1 and an election there would announce nowhere. The same source-routing idea appears on the HAProxy and AdGuard LXCs, which have a VIP on one VLAN and a default route on another.

The CNI is Calico (VXLAN cross-subnet, MTU pinned to 1450). It is installed as K3s addon files, not by Flux.

---

## VIPs and keepalived

Two highly available endpoints use keepalived VRRP on VLAN 10: the AdGuard VIP and the Postgres HAProxy VIP. Each LXC has an eth0 on VLAN 11 for management and an eth1 on VLAN 10 for the VIP. Peers all start as BACKUP with distinct priorities so a simultaneous boot cannot produce two masters, and a track script demotes a holder whose service is down.

---

## Failure surfaces

| What fails | Effect |
|---|---|
| One AdGuard node | The VIP moves in seconds; unnoticed. |
| All AdGuard nodes | Internal names stop resolving; pods flap. Public sites are unaffected (Cloudflare). |
| A MetalLB-speaking worker | Another worker takes the ARP election; a few seconds of loss for that VIP. |
| UCG-Ultra | Everything routed between VLANs stops; the cluster keeps running internally on each VLAN. |
| A "hardened" multi-homed setting | LoadBalancer services intermittently time out for no visible reason; check the four rules above first. |

---

## See also

- **Edge** (this section): how requests enter through Traefik, Cloudflared and the Gateways.
- **Compute & hypervisors** (this section): which guests sit on which VLAN.
- **Networking** (Hardware): the physical gear and firewall boundary.
- **DNS & networking** troubleshooting (Troubleshooting): NXDOMAIN, VIP reachability, sync problems.
