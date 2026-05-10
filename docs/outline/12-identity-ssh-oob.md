# Identity — SSH & OOB access

## Normal access (homelab up)

```
MacBook (wired, VLAN 60)
  → SSH as personal user → any Proxmox node / LXC / K3s VM
  → Proxmox web UI at https://proxmox.infra.xiiisins.com
  → K3s API via kubectl
```

Authentication via Authentik LDAP + SSSD. Personal SSH key required.

## Remote access (away from home)

```
MacBook (any network)
  → Tailscale (split-tunnel, routes 10.0.0.0/8 via homelab)
    → SSH as personal user → any host
    → Proxmox web UI, K3s API, all services
```

Tailscale on MacBook + GL-MT2500 subnet router. No port forwarding through KPN router required.

## OOB access (homelab partially or fully down)

The GL-MT2500 is the out-of-band management device. It connects directly to the KPN router and runs Tailscale independently of all Proxmox nodes. If every Proxmox node is dead, the GL-MT2500 remains reachable.

```
Any device with Tailscale
  → Tailscale → GL-MT2500 (10.0.254.1)
    → SSH into any Proxmox node as recovery user
      → pct enter <lxc-id>     — shell in any LXC
      → qm terminal <vm-id>    — console in any VM
        → restart services, investigate, recover
```

## Break-glass procedure

1. Open 1Password on phone or Mac
2. Find `recovery SSH key` item
3. SSH into any surviving Proxmox node:
   ```bash
   ssh -i /path/to/recovery-key recovery@10.0.254.100
   ```
4. From the Proxmox node, reach any LXC or VM:
   ```bash
   pct enter 125          # Pi-hole LXC
   qm terminal 150        # K3s control plane VM
   ```
5. Investigate and recover
6. Log the break-glass access in the decision log with timestamp and reason

## Wake-on-LAN

All three Proxmox nodes support Wake-on-LAN. If a node is powered off (not just crashed), it can be woken remotely from the GL-MT2500 or any device on the management network:

```bash
# From GL-MT2500 or any host on 10.0.254.0/24
wakeonlan <mac-address-of-node>
```

Node MAC addresses should be documented here once hardware is set up.

| Node | MAC address |
|------|-------------|
| skadi | TBD |
| sigyn | TBD |
| sylvi | TBD |

## Recovery user policy

- Break-glass use must be logged — note time, reason, and actions taken
- Only use the recovery user when normal access is unavailable
- Rotate the recovery SSH key annually or after any use
- Recovery key backup in 1Password is the authoritative copy
