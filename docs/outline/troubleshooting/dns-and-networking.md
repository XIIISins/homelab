<!-- docs/outline/troubleshooting/dns-and-networking.md -->

# DNS & networking

DNS and the L2/L3 fabric fail in ways that masquerade as application bugs. When something "can't connect," rule these out first.

---

## An internal hostname returns NXDOMAIN

**Symptom:** `*.niflheim.xiiisins.com` (or another internal name) won't resolve, or resolves intermittently, on a host or in a pod.

**Cause:** a public resolver crept into the fallback chain. Cloudflare/Google return NXDOMAIN for internal zones, and glibc + CoreDNS cache that NXDOMAIN as authoritative — poisoning resolution until the cache expires.

**Diagnose:** check the resolver config — `/etc/resolv.conf` on the host (or the node backing a CoreDNS replica). Fallbacks must point at the AdGuard peers (`10.0.11.201/202/203`), never at public DNS.

**Fix:** correct the resolver config (the baseline role manages `/etc/resolv.conf` for exactly this reason). For CoreDNS, restart it after fixing the node's resolv.conf: `kubectl rollout restart deploy -n kube-system coredns`.

---

## Can't ping a service VIP

**Symptom:** `ping <MetalLB VIP>` returns "Destination Host Unreachable," so you conclude the service is down.

**Cause:** none — this is expected. MetalLB L2 only ARPs for the VIP; the elected node has no kube-proxy DNAT for an ICMP packet, so the kernel rejects it. The service can be perfectly healthy.

**Fix:** never test a VIP with `ping`. Use TCP against a real port: `curl` or `nc -vz <vip> <port>`.

---

## A host-network process on a K3s node can't reach a VIP

**Symptom:** a systemd binary or host-installed agent on a K3s node hangs trying to dial the Traefik / VictoriaLogs / other MetalLB VIP.

**Cause:** a process in a K3s node's root netns can't reach a MetalLB VIP announced by its own cluster — the announcing node has no kube-proxy DNAT path back to the pod from the root namespace.

**Fix:** point the process at the service's companion **ClusterIP** instead of the VIP. ClusterIP is reachable from the node (it's how `kubectl` works). Example: vlagent on K3s VM hosts uses the `victorialogs-ingest` ClusterIP, not the VL VIP.

---

## A pod can't reach an internal K8s-fronted FQDN

**Symptom:** a pod hangs connecting to something like `authentik.midgard.xiiisins.com` that browsers resolve fine.

**Cause:** AdGuard resolves that FQDN to the Traefik MetalLB VIP for external clients, but a pod dialing the VIP from inside the same cluster tromboning-hangs (same class as the host-network case above).

**Fix:** add a CoreDNS rewrite in `k8s/asgard/infrastructure/coredns-custom/` mapping the FQDN to Traefik's ClusterIP DNS name. Traefik still routes by SNI + Host header; external clients are unaffected. One rewrite per K8s-fronted internal FQDN. Do **not** rewrite FQDNs that point at LXCs/VMs — those aren't behind Traefik.

---

## An AdGuard replica serves stale rewrites

**Symptom:** a DNS rewrite works through one AdGuard node but not another; the VIP gives inconsistent answers.

**Cause:** `adguardhome-sync` reported "Sync done" with a sub-microsecond duration — which is not trustworthy; it can read as "no diff" while actually being stale.

**Diagnose:** compare the rewrite lists directly between origin and replica:
```
# on Saga (origin) vs Mimir/Kvasir
curl .../control/rewrite/list
```

**Fix:** if the origin has the rewrite but a replica doesn't, restart the sync: `systemctl restart adguardhome-sync.service`.

---

## MetalLB LoadBalancer traffic is dropped at a multi-homed worker

**Symptom:** traffic to a LoadBalancer service arrives but gets no reply; `tcpdump` shows asymmetry (request in on `eth1`, nothing back).

**Cause:** one of the four multi-homed-worker kernel settings has been "cleaned up" — strict `rp_filter`, missing `route_localnet`, Calico bound to the wrong NIC, or missing VLAN-20 source-based policy routing.

**Fix:** these are all set unconditionally by the K3s Ansible role (`roles/k3s/tasks/network.yml`); re-running it restores them. Never harden `rp_filter` back to strict or revert Calico autodetection to `firstFound`. See **Network** (Components) for the full landmine list.

## See also

- **Network** (Components) — the VLAN model, MetalLB, and the multi-homed worker fixes in depth.
- **Edge** (Components) — DNS zones, Traefik routing, the CoreDNS rewrite pattern.
