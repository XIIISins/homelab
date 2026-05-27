<!-- docs/outline/components-and-interactions/network.md -->

# Network

Network is the lowest layer with its own moving parts. The physical link (UCG-Ultra, switches, NICs, the 1 GbE fabric) lives in the **Hardware** section; this page is about everything above the wire — VLANs, IP plan, firewall posture, DNS resolution, and the in-cluster networking glue that makes the rest of the stack possible.

Everything above this layer assumes the network works. When something higher up breaks in mysterious ways, this page is one of the first two to suspect.

---

## Internet edge

The KPN-supplied modem is in DMZ mode. Every unsolicited inbound packet (IPv4 + IPv6) lands on UCG-Ultra WAN. KPN itself filters nothing — it's a pass-through. There is no IaC for KPN; any change to it is recorded in the docs or it doesn't exist.

The UCG-Ultra is **the sole firewall policy boundary**. Service exposure happens via UCG port-forwards (DNAT) only — never via per-host `nftables`. The KPN box never sees a port-forward rule; it doesn't have policy to begin with.

---

## VLAN model

The UCG-Ultra carries ten VLANs. No workload lives on an untagged network — every Proxmox host, VM, LXC, and client device is in a VLAN. The VLAN-aware Linux bridge (`vmbr0`) on each Proxmox node carries every tag to every VM and LXC, so VLAN assignment is purely an interface-config concern.

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Hypervisor + NAS management |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-NODE | Asgard K3s nodes (CPs + workers) |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-NODE | Jotunheim K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage / NFS |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

The management subnet is `10.0.254.0/24`. (Earlier drafts had `10.0.1.0/24` and the mistake nearly resulted in a correct iSCSI portal being "fixed." Mentioned here so the muscle memory stays accurate.)

---

## Firewall posture

UCG-Ultra zone defaults are:

- **Internal → Any:** Allow.
- **External → Internal:** Allow Return.
- **Any → Any:** Deny (last).

That posture means workloads can freely reach each other across VLANs by default — segmentation is a tool for blast-radius management, not a friction layer. External traffic gets in only via explicit UCG port-forwards plus the cloudflared tunnel (which lands inside the cluster, not at the UCG).

---

## DNS architecture

### Three zones, one TLS posture per zone

- **`xiiisins.com`** — apex. Externally resolvable via Cloudflare. Publicly-reachable services land here.
- **`midgard.xiiisins.com`** — internal alias for publicly-reachable services. Same backend as the apex; AdGuard resolves it internally → Traefik directly, no Cloudflare tunnel in the path.
- **`niflheim.xiiisins.com`** — internal-only. AdGuard resolves; the UCG firewall blocks any attempt to reach it externally. Operator tooling lives here (`vault`, `netbox`, `semaphore`, `logs`, `metric`).

Each zone gets its own wildcard certificate via cert-manager DNS-01 against the same zone-scoped Cloudflare token. The **Edge** subpage covers cert issuance and routing in detail.

### AdGuard Home topology

Three AdGuard instances run as LXCs on VLAN 11 — Saga, Mimir, Kvasir. Clients only ever talk to a single keepalived VIP at `10.0.10.200`.

- **Saga is the rewrite origin.** All AdGuard rewrites are Terraform-managed in `terraform/adguard/`; the provider writes to Saga's API only — the UI is never hand-edited.
- **Mimir and Kvasir sync from Saga** every minute via `adguardhome-sync`. One minute is short enough that a keepalived failover doesn't serve stale answers.
- **Upstream resolvers** come from baseline configuration on each AdGuard host. The fallback never points at public DNS — public resolvers return NXDOMAIN for internal zones, and glibc + CoreDNS cache that NXDOMAIN as authoritative.

### In-cluster DNS rewrites

A pod inside the K3s cluster can't reach a MetalLB-announced VIP from the same cluster. L2 only ARPs for the VIP; the elected node has no kube-proxy DNAT in its root netns to back-door the packet to the actual pod. Result: a hang.

The fix is a CoreDNS rewrite in `k8s/asgard/infrastructure/coredns-custom/`. Internal FQDNs that AdGuard resolves to the Traefik VIP for *external* clients — `authentik.midgard.xiiisins.com`, `vault.niflheim.xiiisins.com`, `wiki.midgard.xiiisins.com`, and so on — are rewritten cluster-side to Traefik's ClusterIP DNS name. Traefik routes by SNI + Host header to the right backend; external clients are unaffected because their resolution path is AdGuard, not CoreDNS.

The rewrite list grows by one entry per K8s-fronted internal FQDN. It only applies to FQDNs that resolve to a MetalLB VIP — FQDNs pointing at LXCs or VMs (Postgres VIP, AdGuard VIP, Zabbix LXC) don't need rewriting.

---

## MetalLB

K3s clusters use MetalLB in **L2 mode** for in-cluster Service load-balancing. The IP pool sits on a dedicated VLAN — VLAN 20 for asgard, VLAN 30 for jotunheim. The pool is shaped as:

- `10.0.20.10` — Traefik VIP (the most-used entry point; everything internal lands here).
- `10.0.20.11` upward — other LoadBalancer Services (Vault UI, Teamspeak, future workloads).
- `10.0.20.201–203` — worker `eth1` IPs, **outside** the pool. These are real interface IPs, not allocatable VIPs.

Constraints to know:

- **L2 only ARPs for the VIP** — no BGP. The elected node responds to ARP for the VIP and forwards packets via the K3s network stack.
- **Control planes are excluded from MetalLB election** via `nodeSelector`. CPs have a single NIC (no `eth1`), so announcing from a CP would announce to nowhere.
- **MetalLB VIPs do not respond to ICMP.** L2 doesn't synthesise an ICMP responder; the elected node's kernel sees a packet for an IP it doesn't own. Test reachability with TCP (curl, nc) on a defined port — never ping.
- **A new LoadBalancer Service without `loadBalancerIP`** gets a dynamic allocation from the pool bottom, which can shift on Service recreate. Pin a static `loadBalancerIP` for any Service that other things target by IP.

---

## Multi-homed worker landmines

K3s workers are multi-homed precisely so MetalLB can announce on `eth1` (VLAN 20) without disturbing cluster-internal traffic on `eth0` (VLAN 21). Multi-homing surfaces four kernel-level gotchas, all corrected unconditionally by the K3s Ansible role in `roles/k3s/tasks/network.yml`.

1. **Calico autodetection pinned to the cluster CIDR.** Default `firstFound` binds the VXLAN overlay to `eth1` (VLAN 20), which breaks cross-node pod-to-pod traffic. Fix: pin Calico's `nodeAddressAutodetectionV4` to `10.0.21.0/24` (the cluster-node VLAN). Reverting to `firstFound` is the kind of "harmless cleanup" that silently breaks the cluster.
2. **`rp_filter = 2` (loose mode).** Strict mode silently drops MetalLB LoadBalancer traffic arriving on `eth1` — the source IP doesn't match a route via the inbound interface, and strict mode rejects it. Loose mode allows the asymmetric ingress. Do not "harden" back to strict.
3. **`route_localnet = 1`.** L2 only ARPs for the VIP. Without `route_localnet=1`, the elected node's kernel drops packets destined for an IP it doesn't think it owns.
4. **Source-based policy routing for VLAN 20.** Reply packets from MetalLB VIPs source from `eth1`'s VLAN-20 IP. Without policy routing, those replies exit via `eth0` (the default route lives on VLAN 21) → asymmetric path → UCG's stateful firewall drops them. Fix: a systemd oneshot (`vlan20-policy-routing.service`) installs `from 10.0.20.0/24 lookup vlan20` + a VLAN-20 default route in that table.

The four fixes together are what makes the multi-homed L2 model actually work. Skip any one and packets disappear in the kernel — `tcpdump` shows the asymmetry, but no error reaches user space.

---

## Source-based policy routing, generalised

The VLAN-20 fix above is one instance of a broader pattern: any host carrying an IP on a VLAN that differs from its default-route VLAN needs source-based policy routing for replies sourced from that IP. Otherwise: asymmetric reply path → stateful firewall drop.

The pattern is generalised in the `keepalived` Ansible role via the `keepalived_source_policy_routing` option (N-entry capable). Today's consumers:

- **K3s workers** — VLAN 20 IPs vs VLAN 21 default route. Handled by the K3s role.
- **HAProxy + etcd LXCs** (Hlin / Eir / Snotra) — carry the Postgres VIP on VLAN 10 while their default route is VLAN 11. Handled by the keepalived role.

Any future VIP host on a non-default VLAN follows the same pattern. Adding a new entry is a group-vars edit, not a role fork.

---

## Key IPs

Selected static IPs for orientation. The authoritative source is NetBox; this table exists so a reader doesn't have to leave the page to recognise a hostname.

| IP | Host | Purpose |
|----|------|---------|
| `10.0.254.1` | UCG-Ultra | Router, firewall, VLAN routing |
| `10.0.254.11/12/13` | Urd / Verd / Skuld | Proxmox host management |
| `10.0.254.20` | Munin | Synology NAS |
| `10.0.10.200` | AdGuard VIP | DNS resolver (keepalived) |
| `10.0.10.210` | Patroni HAProxy VIP | Postgres entry point |
| `10.0.20.10` | Traefik MetalLB VIP | Internal ingress |
| `10.0.11.201/202/203` | Saga / Mimir / Kvasir | AdGuard trio |
| `10.0.11.230/231/232` | Fulla / Vör / Idunn | Postgres trio |
| `10.0.11.233/234/235` | Hlin / Eir / Snotra | HAProxy + etcd trio |
| `10.0.21.11/12/13` | Göndul / Hlökk / Sigrún | Asgard K3s control planes |
| `10.0.21.21/22/23` | Einherjar-urd/verd/skuld | Asgard K3s workers (`eth0`) |
| `10.0.20.201/202/203` | Einherjar workers (`eth1`) | MetalLB announcement source |

**Cluster CIDRs** (both K3s clusters, by convention): pod `10.42.0.0/16`, service `10.43.0.0/16`.

---

## See also

- **Hardware** section — physical network gear (UCG-Ultra, switches), 1 GbE link layer, cabling.
- **Compute & hypervisors** (this section) — how VMs and LXCs land on these VLANs at create time.
- **Edge** (this section) — Traefik, Cloudflared, cert-manager, the three DNS zones at the certificate layer.
- **Identity & secrets** (this section) — AdGuard rewrite credential management, Cloudflare API token scopes.
