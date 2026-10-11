<!-- docs/outline/troubleshooting/dns-and-networking.md -->

# DNS & networking

Symptoms around names and reachability: internal NXDOMAIN, VIPs that do not answer, tromboning, the multi-homed worker problems, remote access. Reminder: **MetalLB VIPs do not answer ping** (L2 mode answers ARP only). Test with TCP (`curl`, `nc`, `/dev/tcp`), never `ping`.

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
```

---

## 1. First diagnostics (read-only)

```bash
# Ask the VIP, then each node, directly. They must agree.
for s in 10.0.10.200 10.0.11.201 10.0.11.202 10.0.11.203; do printf '%-13s' $s; dig +short +time=2 +tries=1 @$s netbox.niflheim.xiiisins.com | tr '\n' ' '; echo; done
# Expect 10.0.20.10 (Traefik VIP) from every one

dig @10.0.10.200 netbox.niflheim.xiiisins.com | grep -E "status:|ANSWER:"       # NOERROR vs NXDOMAIN
curl -s -m 8 https://smoketest.niflheim.xiiisins.com/anything; echo               # "smoketest ok" = rewrite + Traefik + backend work end to end
timeout 3 bash -c '</dev/tcp/10.0.20.10/443' && echo "Traefik VIP open" || echo "Traefik VIP not answering"

# Where does THIS machine send DNS?
cat /etc/resolv.conf; resolvectl status 2>/dev/null | head -20
```

Which AdGuard node holds the VIP, and are the three healthy?

```bash
for ip in 10.0.11.201 10.0.11.202 10.0.11.203; do
  echo "== $ip"; $SSH ansible@$ip 'ip -br a show eth1 | grep -c "10.0.10.200" | sed "s/^1$/HOLDS the VIP/;s/^0$/-/"; systemctl is-active AdGuardHome keepalived adguardhome-sync 2>&1 | tr "\n" " "; echo'
done
```

(`adguardhome-sync` only runs on Saga, `10.0.11.201`; the other two report `inactive` for it. That is normal.)

---

## 2. An internal name returns NXDOMAIN or the wrong address

1. **Is the record in Git?** `grep -n "<name>" terraform/adguard/rewrites.tf`. If not, it was never created or was hand-added in the UI (the UI copy is outside the spec). Add it via PR and apply (**Everyday operations** §9).
2. **Do origin and replicas agree?** Compare the rewrite counts and the specific record:

   ```bash
   for ip in 10.0.11.201 10.0.11.202 10.0.11.203; do
     printf '%s ' $ip; curl -s -m 5 -u "$ADGUARD_USERNAME:$ADGUARD_PASSWORD" http://$ip/control/rewrite/list | jq 'length'
   done
   curl -s -u "$ADGUARD_USERNAME:$ADGUARD_PASSWORD" http://10.0.11.201/control/rewrite/list | jq -r '.[]|select(.domain|test("<name>"))'
   ```

   A replica missing a record that Saga has: restart the sync (`ssh … ansible@10.0.11.201 'sudo systemctl restart adguardhome-sync'`), wait a minute, compare again. A "Sync done" log line with a duration in nanoseconds does not prove anything; diff the lists.
3. **Is a stale negative answer cached?** A name queried before it existed is negative-cached by AdGuard **and** clients for the zone's negative TTL (30 minutes for the apex). Clear AdGuard's cache (UI: Settings → DNS settings → Clear cache; or `curl -s -X POST -u "$ADGUARD_USERNAME:$ADGUARD_PASSWORD" http://10.0.11.201/control/cache_clear`) and the client's (`resolvectl flush-caches`, or `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder` on macOS).
4. **Is a public resolver in the machine's fallback chain?** Public resolvers return NXDOMAIN for internal zones and clients cache that as truth. `cat /etc/resolv.conf` on the failing host: fallbacks may be the peer AdGuard nodes (`10.0.11.201-203`), **never** `1.1.1.1`/`8.8.8.8`. Fix in `baseline_nameservers` (Ansible baseline) and re-run the baseline for that host.
5. **Tailnet client?** MagicDNS split-resolves `niflheim` and `midgard` to the VIP only when connected through a subnet router: `tailscale status`, and the client must accept routes/DNS.

## 3. Queries time out after an AdGuard restart

AdGuard uses DoH upstreams with bootstrap IPs. If the bootstrap addresses are unreachable, AdGuard starts but every query times out.

```bash
$SSH ansible@10.0.11.201 'sudo journalctl -u AdGuardHome -n 40 --no-pager'
# Temporary: override adguard_upstream_dns (default in roles/adguard/defaults/main.yml) in group_vars/adguard.yml to include 1.1.1.1, then re-run asgard-adguard.yml --limit <node>
```

Change it back afterwards (PR). When **all** AdGuard nodes are down, internal names stop resolving everywhere and pods flap; fix AdGuard first (**Hosts & Proxmox** for a dead node).

## 4. The AdGuard VIP: failover and dual-MASTER

```bash
for ip in 10.0.11.201 10.0.11.202 10.0.11.203; do $SSH ansible@$ip 'echo -n "$(hostname): "; ip -br a show eth1 | grep -c 10.0.10.200; sudo journalctl -u keepalived -n 5 --no-pager | tail -3'; done
```

Exactly one node should hold `10.0.10.200`. Two holders (dual-MASTER) means the VRRP adverts are being rejected: `received an invalid passwd from <peer>` in `keepalived` logs means the `auth_pass` differs between nodes, or another keepalived cluster on VLAN 10 uses the same VRID (AdGuard uses **51**, the Postgres VIP **61**; never reuse). Fix the shared variable and re-run `asgard-adguard.yml`/`asgard-haproxy-etcd.yml` one node at a time. A node whose AdGuard is down is demoted by a track script; restart `AdGuardHome` and it returns to BACKUP.

If failover left stale ARP: clients settle in under 30 seconds; `arp -d 10.0.10.200` on the client forces it.

## 5. A pod cannot reach an internal name that works from your laptop

**Cause:** MetalLB L2 mode hairpins badly when a pod dials a VIP on the same VLAN as its node (the "trombone"). Pods are supposed to use in-cluster paths via a CoreDNS rewrite.

```bash
kubectl run dnstest --rm -it --restart=Never --image=busybox:1.36 -- nslookup <name>.niflheim.xiiisins.com
kubectl -n kube-system get cm coredns-custom -o yaml | grep -n "rewrite\|override\|server"
```

If the name should be rewritten to `traefik.traefik.svc.cluster.local` and is not: add it to `k8s/asgard/infrastructure/coredns-custom/` and land it through Git. In that ConfigMap, keys ending `*.override` are placed **inside** the default server block (for `rewrite`, `forward`, `hosts` directives); keys ending `*.server` define whole new server blocks. A bare plugin directive in a `*.server` key is a syntax error. After node `resolv.conf` changes, `kubectl -n kube-system rollout restart deploy/coredns`.

A host process on a K3s node (not a pod) cannot dial a MetalLB VIP from the same cluster; give it a companion ClusterIP service (example: log shippers use the `victorialogs-ingest` ClusterIP).

## 6. LoadBalancer traffic intermittently times out (the multi-homed worker rules)

Workers have eth0 (VLAN 21) and eth1 (VLAN 20, MetalLB). Four settings keep that working. **Do not "harden" any of them.**

```bash
W=<worker-ip>
$SSH ansible@$W 'sysctl net.ipv4.conf.all.rp_filter net.ipv4.conf.default.rp_filter net.ipv4.conf.all.route_localnet'   # rp_filter = 2 (loose), route_localnet = 1
$SSH ansible@$W 'ip rule | grep -i vlan20; ip route show table vlan20; systemctl is-active vlan20-policy-routing'          # from 10.0.20.0/24 lookup vlan20; default via 10.0.20.1 dev eth1
kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}{"\n"}'                   # cidrs=[10.0.21.0/24], NOT firstFound
```

| If you see | Cause | Fix |
|---|---|---|
| `rp_filter` = 1 | Strict reverse-path filtering drops LoadBalancer traffic on eth1 | Re-run `asgard-k3s.yml --limit <worker> --tags k3s` (network task) from Frigg |
| `route_localnet` = 0 | MetalLB only ARPs the VIP; the kernel drops packets to the unbound address | same |
| No `vlan20` rule/route, unit failed | Replies exit eth0 → asymmetric → the UCG firewall drops them | `sudo systemctl restart vlan20-policy-routing` then fix via the play |
| Calico bound to eth1 / `firstFound` | Cross-node overlay breaks | Patch the live `Installation` (`kubectl patch --type=json`) and the addon template; read the live object after: the addon controller merges, so removed fields can linger |

Which worker is announcing a VIP? `kubectl -n metallb-system logs -l app.kubernetes.io/component=speaker --tail=200 | grep -i "<vip>"`. MetalLB's `L2Advertisement` excludes control planes (no eth1) by node selector.

Pinned LB addresses (`.10` Traefik, `.11` Vault UI, `.12` Teamspeak) are fixed in the manifests; a chart LoadBalancer without `loadBalancerIP` takes one from the pool bottom and can collide. Check: `kubectl get svc -A | grep LoadBalancer`.

The same source-routing problem occurs on dual-NIC LXCs (AdGuard, HAProxy: VIP on VLAN 10, default route on VLAN 11). Symptom: replies to the VIP never arrive. Check on the LXC: `systemctl status vlan10-policy-routing; ip rule`. If the unit is failed after a reboot (lost the boot race), `sudo systemctl restart vlan10-policy-routing`; the keepalived role waits for the gateway before applying it.

## 7. Calico / cross-node pod traffic

```bash
kubectl get tigerastatus                                   # all Available
kubectl get ippools.crd.projectcalico.org                   # 10.42.0.0/16 must exist
kubectl get pods -n calico-system -o wide
kubectl -n tigera-operator logs deploy/tigera-operator --tail=30
```

Same-node traffic works but cross-node does not, with nodes Ready: suspect Calico (autodetection or a wiped datastore: IP pools missing, `Installation object is terminating`). **A wiped datastore is an emergency: escalate** and use **K3s & Calico upgrade** (recovery paragraph).

## 8. Remote access (Tailscale)

```bash
tailscale status | head -20                                  # peers; "cannot relay traffic" / health warnings appear at the bottom
tailscale status --json | jq '.Health'
tailscale ping bifrost 2>&1 | head -3                         # NOTE: ping succeeds even when ACLs deny traffic; test with TCP
timeout 3 bash -c '</dev/tcp/10.0.11.21/80' && echo ok || echo "blocked/no route"     # is a homelab address reachable via the subnet router?
```

- **A homelab address is unreachable from a tailnet node:** the node does not accept routes (`tailscale debug prefs | grep RouteAll` is false). Opt-in per host with `tailscale_accept_routes: true`; **never** on Munin.
- **"Cannot relay traffic" on a router/exit node:** `net.ipv4.ip_forward` and `net.ipv6.conf.all.forwarding` must both be 1 (`/etc/sysctl.d/99-tailscale.conf`).
- **A rebuilt Tailscale LXC cannot join:** the 90-day auth key expired. `terraform -chdir=terraform/tailscale apply` (it re-mints invalid keys and rewrites Vault), then re-run `asgard-tailscale.yml`.
- **Munin dropped off the LAN but is still online in the tailnet:** the DSM package upgrade or UI login applied bad flags. Reach it on its Tailscale IP, `sudo tailscale down` (restores the LAN; do **not** power-cycle), then re-run the bring-up from **Teardown & rebuild** Appendix C. If subnet routing silently stopped after a package upgrade: `sudo /var/packages/Tailscale/target/bin/tailscale configure-host; sudo synosystemctl restart pkgctl-Tailscale.service`.

## 9. After a Proxmox node hardware swap: no LAN after boot

The NIC name changed (`nic0` vs `enp45s0`) and `/etc/network/interfaces` still names the old one. **Needs the physical console.** At the console: `ip -br link`, edit `/etc/network/interfaces` to the new name, `ifreload -a`. Prevention: capture `ip -br link` on the old hardware first.

## 10. The UCG-Ultra / KPN

Both are manual and out of IaC. Confirm the zone rules (`Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` last) and port-forwards from the documented list in **Procedures → Teardown & rebuild** §1.1. Editing a policy in the UCG UI moves it to the bottom of its list: re-check that allows sit above the final deny (the AIOps hosts' egress policies break if an allow falls below `Egress Default Deny`), and allow about a minute for a change to take effect.

---

## See also

- **Network** (Components) and **Networking** (Hardware).
- **Identity & edge** (this section): when the name resolves but the login or certificate fails.
- **Hosts & Proxmox** (this section): a dead AdGuard node.
