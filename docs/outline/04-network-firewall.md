# Network — firewall rules

All rules configured on the GL-MT2500 via OpenWrt. Rules follow least-privilege — each VLAN can only reach what it needs.

## Rules by VLAN

### Management (VLAN 254) — full access

```
10.0.254.0/24 → any: ALLOW
```

Management is the admin plane. Full access everywhere. Only reachable from the MacBook's static IP on VLAN 60 and via Tailscale OOB.

### Personal (VLAN 60) — internet + services + scoped management

```
10.0.60.0/24 → internet: ALLOW
10.0.60.0/24 → 10.0.40.0/24 (services): ALLOW
10.0.60.10   → 10.0.254.0/24 (management): ALLOW   ← MacBook static IP only
10.0.60.0/24 → 10.0.10.0/24 (must-run): DENY
10.0.60.0/24 → 10.0.20.0/24 (k3s): DENY
10.0.60.0/24 → 10.0.30.0/24 (storage): DENY
```

Management access scoped to MacBook's static IP (`10.0.60.10`). Game PC on VLAN 60 cannot reach Proxmox or GL-MT2500 admin interface.

### Must-run (VLAN 10) — storage, management, internet

```
10.0.10.0/24 → internet: ALLOW
10.0.10.0/24 → 10.0.30.0/24 (storage): ALLOW
10.0.10.0/24 → 10.0.254.0/24 (management): ALLOW
10.0.10.0/24 → 10.0.20.0/24 (k3s): DENY
10.0.10.0/24 → 10.0.40.0/24 (services): DENY
10.0.10.0/24 → 10.0.60.0/24 (personal): DENY
```

### K3s (VLAN 20) — storage, services, management, internet

```
10.0.20.0/24 → internet: ALLOW
10.0.20.0/24 → 10.0.30.0/24 (storage): ALLOW
10.0.20.0/24 → 10.0.40.0/24 (services): ALLOW
10.0.20.0/24 → 10.0.254.0/24 (management): ALLOW
10.0.20.0/24 → 10.0.10.0/24 (must-run): DENY
10.0.20.0/24 → 10.0.60.0/24 (personal): DENY
```

### Storage (VLAN 30) — isolated

```
10.0.30.0/24 → 10.0.254.0/24 (management): ALLOW
10.0.30.0/24 → any other: DENY
```

NFS traffic only. No VLAN-initiated connections to anything except management for monitoring.

### Services (VLAN 40) — reachable inbound only

```
10.0.40.0/24 → 10.0.20.0/24 (k3s): ALLOW
10.0.40.0/24 → any other homelab: DENY
```

MetalLB IPs are reachable from VLAN 60 (personal) and from Cloudflare Tunnel (which terminates in K3s). Services cannot initiate connections to other VLANs except back to K3s pods.

## Internet access

All VLANs (except storage) route to the internet via the GL-MT2500, which NATs through the KPN router. K3s pods use the K3s internal network (`10.42.0.0/16`) and worker VMs masquerade pod traffic behind their `10.0.20.x` IP. No port forwarding through the KPN router is needed — external access goes through Cloudflare Tunnel or Tailscale.
