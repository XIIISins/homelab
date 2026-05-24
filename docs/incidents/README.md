<!-- docs/incidents/README.md -->

# Incident log

Per-incident retrospectives. Each entry: trigger → diagnosis → resolution → root-cause pattern. Lessons from these incidents are encoded as rules in [`CLAUDE.md`](../../CLAUDE.md) "Known gotchas" and decision rows in [`docs/operations/decisions.md`](../operations/decisions.md).

## By date

| Date | Phase | Summary | File |
|------|-------|---------|------|
| 2026-05-14 | pre-rebuild | etcd storm cascade (≈15h). Adding worker eth1 + reboot triggered etcd IO storm on old Urd, exposed 6 latent classes (Calico autodetection, rp_filter, iSCSI session pinning, MetalLB L2 election, MGMT subnet doc error, tigera SELinux). | [etcd-storm-cascade](2026-05-14-etcd-storm-cascade.md) |
| 2026-05-15 | 5c carry-over | tigera-operator SELinux denial on `/var/lib/calico/mtu` (upstream #7851). Fixed via explicit `mtu: 1450` in Calico Installation CR. | [tigera-operator-selinux](2026-05-15-tigera-operator-selinux.md) |
| 2026-05-16 | 5f | Factorio LXC end-to-end deploy. 7 bugs in factorio/sftpgo roles + minimal-template gaps fixed in-session. | [factorio-deploy](2026-05-16-factorio-deploy.md) |
| 2026-05-17 | 5c teardown+rebuild | Deliberate asgard teardown + rebuild. 9 architectural findings closed (per-component config Kustomizations, sealed-keys backup, idempotency, multi-homed networking, CP rebuild override, restart-handler safety). | [asgard-rebuild](2026-05-17-asgard-rebuild.md) |
| 2026-05-17 evening | 5e | Authentik + Redis deploy. First real K8s workload on rebuilt cluster; surfaced DNS resolv.conf class, env-vs-values precedence, CP-sizing-too-small, brand uniqueness, and more. | [authentik-redis](2026-05-17-evening-authentik-redis.md) |
| 2026-05-21 | Phase 4a + Urd refresh | Urd hardware refresh (MSI Cubi) + CP NoSchedule taint deploy. Surfaced CSI eviction footgun, floating-Helm-pin outages (metallb 0.16, synology-csi 0.11.2), ext4 journal abort under iSCSI timeout. | [urd-refresh-phase-4a](2026-05-21-urd-refresh-phase-4a.md) |
| 2026-05-21 evening | Phase 4a cleanup | Hostkey zero-byte from NUC7 crash mid-write, iSCSI orphan-record reconciliation, Authentik restart-pattern triage. | [phase-4a-cleanup](2026-05-21-evening-phase-4a-cleanup.md) |
| 2026-05-22 | Phase 4b + worker rebuild | Göndul Verd → Urd migration; einherjar-urd worker rebuild. Surfaced orphan-LVs class, iproute 6.17 missing `/etc/iproute2/rt_tables`, Vault chart required pod anti-affinity. | [phase-4b-worker-rebuild](2026-05-22-phase-4b-worker-rebuild.md) |
| 2026-05-22 evening | Phase 5e.1 | Traefik + Gateway API + cert-manager + Authentik HTTPS cutover. 8 findings (Traefik v39+ syntax, Gateway listener-port matching, NET_BIND_SERVICE, anti-affinity + maxSurge deadlock, Helm rollback-removes-values, MetalLB no-ICMP, schema validation gaps). | [phase-5e1-traefik](2026-05-22-evening-phase-5e1-traefik.md) |
| 2026-05-23 | Phase 5e.2 | HTTPRoute migration to midgard Gateway initially missed at 5e.2.f close; surfaced 12h later by Terraform reachability check. | [phase-5e2-httproute-migration](2026-05-23-phase-5e2-httproute-migration.md) |
| 2026-05-23 | post-5e.3 | Authentik PG DNS resolution flapping recurrence (58 restarts/24h). Symptom-mitigated by IP-stopgap pointing at Fulla `10.0.11.230`; underlying class still open. | [authentik-pg-dns-flapping](2026-05-23-authentik-pg-dns-flapping.md) |
| 2026-05-24 | post-5e.3 | Cloudflare zone re-harden after 5e.3.c global-disable. Per-path WAF skip ruleset for protocol-public endpoints; surfaced provider v5 import discriminator + per-app OIDC discovery path. | [cloudflare-reharden](2026-05-24-cloudflare-reharden.md) |
| 2026-05-23 | 5e.3.c | Cloudflare bot protections blocked Tailscale WebFinger probe (403). Mitigated by global-disable initially; properly fixed in 2026-05-24 entry above. | [cloudflare-bot-webfinger](2026-05-23-cloudflare-bot-webfinger.md) |
| 2026-05-24 | 5g.2 close | HAProxy + keepalived VIP `10.0.10.210` + Authentik cutover + failover validation. Generic data-driven roles built. 5 findings: bg-worktree vs local TF state, VRID collision with AGH (spacing-by-10 convention), pg_hba `replication` separate from `all`, `systemctl kill` doesn't actually kill patroni, fast-catchup vs forced-rewind framing. | [5g2-haproxy-keepalived-vip](2026-05-24-5g2-haproxy-keepalived-vip.md) |
