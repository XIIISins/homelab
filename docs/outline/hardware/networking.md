<!-- docs/outline/hardware/networking.md -->

# Networking

The physical network is small on purpose: one router/firewall, two switches that do nothing but carry tags, and an ISP box configured to get out of the way. Policy lives in exactly one place.

---

## The devices

| Device | Role |
|---|---|
| **UCG-Ultra** | Router, VLAN routing, zone-based firewall. Static public WAN IP, no CGNAT. The sole firewall policy boundary. |
| **KPN Experia Box** | ISP modem/router in exposed-host (DMZ) mode. Forwards all unsolicited inbound traffic, IPv4 and IPv6, to the UCG-Ultra WAN address. Keeps doing outbound NAT only for the `192.168.2.0/24` set-top and family devices. |
| **Two unmanaged switches** | Layer-2 only. They pass 802.1Q tags transparently and have no configuration to drift. |

The KPN box is consumer hardware without a useful API, so it is **never in IaC**: its current configuration is recorded in the docs, and a change that is not written down does not exist. Port-forwards for new internet-facing services are created on the UCG-Ultra only (the forward plus its generated allow rule); the KPN box is not touched.

---

## Link layer

- **Everything is 1 GbE.** Nodes, NAS and uplinks. No 2.5 GbE is planned.
- Which room a device sits in only affects cabling: the clients (MacBook dock, game PC, Hue bridge) connect via one switch and the Proxmox nodes plus Munin via the other, both trunking back to the UCG-Ultra.
- Each Proxmox node uses one uplink with a VLAN-aware bridge. K3s workers are the one place a guest is multi-homed: eth0 on the node VLAN and eth1 on the MetalLB VLAN (explained on **Network**, Components).

---

## Firewall boundary

The UCG-Ultra's zone-based firewall, in order:

| From | To | Policy |
|---|---|---|
| Internal | Any | Allow |
| External | Internal | Allow return traffic only |
| Hotspot | Internal | Allow return traffic only |
| Any | Any | Deny (last) |

Every homelab VLAN sits in the Internal zone, so inter-VLAN traffic is allowed by default and the protection is at the edge. Nodes do not run host firewalls (`firewalld` is disabled on the K3s nodes); the UCG-Ultra is the firewall. Two AIOps hosts (the diagnosis agent and the Discord bot) get tighter, egress-only allow-lists with a final deny, because they run an LLM-driven agent and a chat bot with outbound reach.

---

## Out-of-band access

Tailscale runs **on Munin itself** (a DSM package), advertising the `10.0.0.0/16` supernet. It does not depend on Proxmox, K3s or any VM being up, so a complete hypervisor outage still leaves a path into the management network. Day-to-day remote access goes through the Tailscale LXCs (two subnet routers and an exit node) instead; Munin is the survives-everything fallback. Its auth key is minted by hand in the Tailscale UI and kept in 1Password, deliberately outside Vault, because Vault may be what is broken.

---

## See also

- **Hardware** (this section): the physical overview.
- **Network** (Components): VLANs, addressing, DNS, MetalLB and the multi-homed-worker rules.
- **Edge** (Components): how public and internal traffic enters the cluster.
- **DNS & networking** troubleshooting (Troubleshooting): VIP reachability and DNS faults.
