<!-- docs/outline/hardware/networking.md -->

# Networking

The physical network gear and how the boxes are wired. This page covers the hardware — the router, the switches, the ISP boundary, and the out-of-band path. The logical network that runs over it (VLANs, IP assignments, firewall policy, DNS) lives in the **Network** page under Components & interactions.

---

## The gear

| Device | Role |
|---|---|
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Router, firewall, VLAN routing. Static WAN IP. The **sole firewall policy boundary**. |
| KPN Experia Box | ISP modem/router, run in DMZ ("exposed host") mode — forwards all unsolicited inbound (IPv4 + IPv6) at the UCG and filters nothing of its own. |
| Dumb switch ×2 | Unmanaged switches that pass VLAN tags transparently. No routing, no policy. |
| Tailscale on Synology (DSM package) | Out-of-band access that survives a complete homelab failure. |

---

## Link layer

Every link is **1 GbE**. No 2.5 GbE is planned.

The switches are unmanaged — they fan a port out to several devices and forward 802.1Q tags without inspecting them. All VLAN routing and all firewall policy happen on the UCG-Ultra. Each Proxmox node presents a single VLAN-aware bridge that carries every tag to the VMs and LXCs on that node, so a single physical uplink per node is enough to serve every VLAN that node participates in.

The physical cabling layout is drawn on the **Hardware** overview.

---

## The ISP boundary

The KPN Experia Box is the only thing between the homelab and the internet, and it's deliberately kept dumb:

- It runs in **DMZ / exposed-host mode**, pointing all inbound traffic (both IPv4 and IPv6) at the UCG-Ultra's WAN port.
- It does **outbound NAT only for the family device range** (`192.168.2.0/24`) — set-top box, family devices — which stay on the KPN LAN, untouched by the homelab.
- It holds **no port-forwards and no firewall rules**. Every forwarding and policy decision lives one hop further in, on the UCG.

The KPN box is never in IaC. Because it can't be expressed as code, any change to it is recorded in these docs — or, by the homelab's own rule, it doesn't exist.

---

## Out-of-band access

The control path of last resort is the **Synology's DSM Tailscale package** (Munin). It matters because it depends on nothing else in the homelab — not the Proxmox nodes, not the K3s clusters, not the UCG's policy engine being sane. If the whole virtual stack is down or misconfigured, the Synology is still reachable over the tailnet, which is enough to begin recovery — including reaching the NAS itself when its LAN is broken.

It also acts as a **Tailscale subnet router**, advertising the `10.0.0.0/16` homelab supernet onto the tailnet so remote access reaches internal hosts without a per-host client. Critically it runs **advertiser-only** — `--advertise-routes` but **never** `--accept-routes` (and no exit-node consumption). The advertised supernet *contains the NAS's own subnet*, so accepting routes would divert Munin's own LAN traffic into the tailnet and cut its LAN entirely (online in tailnet, dead on the LAN IP). Bring it up via the CLI authkey, not the package web-UI login (which applies default flags). If it ever does cut its own LAN, the break-glass is to reach it over its tailnet IP and `tailscale down`.

---

## See also

- **Network** (Components & interactions) — the VLAN table, IP assignments, firewall posture, and DNS architecture that ride on this gear.
- **Edge** (Components & interactions) — how externally-reachable services get out (Cloudflared tunnel) rather than via inbound port-forwards.
- **Hardware** (this section) — the physical cabling topology.
