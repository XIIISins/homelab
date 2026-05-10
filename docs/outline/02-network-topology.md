# Network — physical topology

## Design rationale

The KPN ISP router is closed and provides no VLAN or advanced routing support. Rather than fighting it, a GL.iNet GL-MT2500 sits behind it and handles all homelab networking. The KPN router is untouched — family devices (WiFi, TV, phones) work exactly as before.

## Physical layout

```
Internet
  └── KPN router (192.168.2.0/24) — untouched
        ├── Family WiFi, TV, phones — unchanged
        └── GL-MT2500 WAN (gets 192.168.2.x from KPN DHCP)
              ├── LAN 1 → Dumb switch (downstairs)
              │             ├── skadi  — 10.0.254.100
              │             ├── sigyn  — 10.0.254.101
              │             ├── sylvi  — 10.0.254.102
              │             └── Synology — 10.0.254.103
              └── LAN 2 → Dumb switch (upstairs)
                            ├── MacBook dock — 10.0.60.x (static)
                            └── Game PC — 10.0.60.x
```

## Why dumb switches work

Tagged VLAN frames (802.1Q) pass through unmanaged switches transparently — the switch forwards frames without inspecting or stripping the VLAN tag. Proxmox handles VLAN tagging in software via VLAN-aware bridges. DSM on the Synology handles VLAN tagging via its network interface configuration. No managed switch required.

## Hardware

| Device | Role | Cost |
|--------|------|------|
| GL.iNet GL-MT2500 | Router, firewall, Tailscale OOB | ~€50 |
| Existing dumb switch (downstairs) | Connects Proxmox nodes + Synology | €0 |
| Existing dumb switch (upstairs) | MacBook dock + game PC | €0 |

Total new hardware spend: **~€50**

## Personal device access

| Device | Physical connection | Homelab access |
|--------|-------------------|---------------|
| MacBook | Wired via dock → upstairs switch → GL-MT2500 LAN 2 | Direct on VLAN 60 |
| MacBook (undocked) | KPN WiFi (192.168.2.x) | Via Tailscale split-tunnel |
| Game PC | Wired → upstairs switch → GL-MT2500 LAN 2 | VLAN 60 direct + Tailscale for homelab routes |

## OOB access path

The GL-MT2500 runs Tailscale as a subnet router advertising `10.0.0.0/8`. It connects directly to the KPN router and is independent of all Proxmox nodes. If the entire homelab fails, the GL-MT2500 remains reachable via Tailscale and provides access to the management network for recovery.

See [Identity — SSH & OOB access](./12-identity-ssh-oob.md) for the full recovery procedure.
