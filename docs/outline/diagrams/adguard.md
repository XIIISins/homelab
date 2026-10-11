<!-- docs/outline/diagrams/adguard.md -->

# AdGuard diagram

How DNS works for the whole lab: every client asks one address, three AdGuard containers share it, and one of them (Saga) is the origin the others copy.

![AdGuard Home: DNS for the whole lab](/api/attachments.redirect?id=70798eb3-05ec-4819-b852-a2e056729d5a)

---

## How to read it

- **Left: who asks.** LAN clients get the DNS address from DHCP or static config. Tailnet clients use MagicDNS split DNS, and the Tailscale subnet routers forward their queries to the same address.
- **Top middle: the one address.** keepalived hands it to the first healthy node in line: Saga, then Mimir, then Kvasir, one per Proxmox host.
- **The three containers.** Saga is the origin and runs `adguardhome-sync`, which copies its configuration to Mimir and Kvasir every minute.
- **Below: who changes them.** Terraform writes the DNS rewrites onto Saga (never through the UI). Ansible installs AdGuard and keepalived.
- **Right: where answers come from.** Cloudflare is authoritative for the public apex, so AdGuard does not serve it. Rewrites send internal app names to Traefik. Everything else goes to the Quad9 and Cloudflare DoH upstreams.
- **Bottom strip: one query, in order.** Rewrite first, then known-client lookup (DHCP, ARP, mDNS), then block lists (answered NXDOMAIN), then a DoH upstream, with the result cached.
- **Failure shape.** One node down: the next in line takes the address within seconds. All three down: rewrites stop resolving.

---

## Files

The editable source and a vector copy for zooming in or reusing elsewhere (export settings are on the **Diagrams** page).

[adguard.excalidraw 55000](/api/attachments.redirect?id=21808e29-52aa-402b-a50c-d14d15fc90f4)

[adguard.svg 62761](/api/attachments.redirect?id=b46a9899-a90e-4db0-9fa0-2f524e53832f)

---

## See also

- **Network** (Components): the DNS section and the VIPs.
- **Edge** (Components): the three DNS zones and how names reach Traefik.
