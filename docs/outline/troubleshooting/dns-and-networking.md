<!-- docs/outline/troubleshooting/dns-and-networking.md -->

# DNS & networking

Symptoms around names and reachability: internal NXDOMAIN, VIPs that do not answer, tromboning, and the multi-homed worker problems.

---

## First diagnostics

```
dig @10.0.10.200 netbox.niflheim.xiiisins.com      # ask the VIP directly
dig @10.0.11.201 … ; dig @10.0.11.202 … ; dig @10.0.11.203 …   # each node
curl https://smoketest.niflheim.xiiisins.com/anything   # one-step check: rewrite + Traefik + backend
```

Test a VIP with TCP, never ping: MetalLB answers ARP only.

## Symptoms

| Symptom | Likely cause | What to do |
|---|---|---|
| An internal name returns NXDOMAIN on one machine only | A public resolver sits in that machine's fallback chain; public resolvers say NXDOMAIN for internal zones and clients cache it as authoritative | Remove the public resolver. Fallbacks may point at the peer AdGuard nodes, never public ones. |
| A brand-new public hostname is NXDOMAIN on the LAN for ~30 minutes | The name was queried before the record existed and negative-cached by AdGuard and clients (SOA negative TTL 1800 s) | Wait, or flush the AdGuard cache and the client cache. |
| A rewrite was added but a replica does not have it | `adguardhome-sync` shows "Sync done" in sub-microsecond time, which does not prove anything | Compare the rewrite list on origin and replicas directly; restart the sync service if they differ. Rewrites are made in Terraform, not the UI. |
| Stale DNS answers after a failover | Sync lag, or the sync stopped when Saga went down | Replicas freeze at last-good config while Saga is down. When Saga returns, its state overwrites any replica edit. |
| Two nodes answer ARP for the AdGuard VIP | Dual-MASTER during a keepalived change (differing `auth_pass`) | Bounded; settles once both sides have the same key. Two keepalived clusters on one VLAN must use different VRIDs. |
| Queries time out after an AdGuard restart | The DoH upstream bootstrap addresses are unreachable | Temporarily add a plain upstream resolver. |
| A pod cannot reach an internal name that works from a laptop | Pod-to-MetalLB-VIP traffic hairpins badly in L2 mode | Add an in-cluster CoreDNS rewrite to the Traefik service name (`coredns-custom`). `*.override` keys go inside the default server block; `*.server` keys define whole new blocks. |
| A host-network process on a K3s node hangs dialling a VIP | A node's root namespace cannot reach a MetalLB address in its own cluster | Use a ClusterIP companion service. |
| LoadBalancer traffic intermittently times out | A multi-homed worker setting was "hardened": strict `rp_filter`, `route_localnet` off, Calico bound to eth1, or the VLAN 20 policy routing missing | Restore the four settings in the Ansible `k3s` role. |
| Replies from a VIP never arrive on a dual-NIC LXC | Asymmetric routing; the VIP's VLAN has no source-based route | The keepalived role's source policy routing. |
| A VIP on a pinned address changed | A chart's LoadBalancer service did not pin its IP and took one from the pool bottom | Every service pins its address. |
| After a Proxmox node swap, no LAN after boot | The NIC name changed (`nic0` vs `enp45s0`) and `/etc/network/interfaces` references the old one | Needs the physical console. Capture `ip -br link` beforehand. |

## Calico

- The `Installation` object is merged by the addon controller, so a removed field can linger. After changing autodetection, read the live object and patch it.
- The MTU is pinned (1450) because the operator cannot read the MTU file under SELinux. Do not remove it without a replacement fix.
- Cross-node pod traffic failing while nodes are Ready and same-node traffic works suggests a wiped Calico datastore (IP pools gone): see **K3s & Calico upgrade**.

---

## See also

- **Network** (Components): the model behind these rules.
- **Networking** (Hardware): the physical boundary.
- **Identity & edge** (this section): when the name resolves but the login fails.
