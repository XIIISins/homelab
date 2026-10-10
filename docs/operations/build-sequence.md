<!-- docs/operations/build-sequence.md -->

# Build sequence

The route to done: one concise row per phase, grouped by area. Each phase's full closing narrative, findings and commit references are kept verbatim in [`build-sequence-history.md`](build-sequence-history.md) (find the row by phase id); open work items live in [`open-questions.md`](open-questions.md) and architectural choices in [`decisions.md`](decisions.md).

Status: ✅ done · 🟡 built, with open work · 🔲 not started or deferred · ✖ dropped.

*Renumbering (2026-05-26): former Phases 6/7/8 became 7/8/9 to make room for the Vault OIDC phase. Retros keep their original phase labels. Phase 7 was dropped 2026-10-05 and numbering was not shifted again.*

## What's left (2026-10-09)

**Built, still open**
- **5h Jellyfin + media automation:** J5 acceptance (`pct migrate`, PBS restore, real 4K/AV1/PGS playback, smoke failure path), the Sonarr config-loss recovery test, household accounts and the Tailscale ACL.
- **10f autonomous healing:** the ~14-day canary soak read-out is due about 2026-10-17.
- **10g rebuild loop:** stage A (canaries) proven; replica and worker stages not built. PBS datastore capacity is no longer a prerequisite (67% on 2026-10-10).
- **10h predictive changes and PR author:** all three parts live; exit criteria not yet met.
- **10i rightsizing:** 10i2 (Toolbelt data + findings) onward; needs about 7 days of VPA history.
- **10a offsite node `do1`:** cleanup done 2026-10-10; left: delete the stale Tailscale machine records and revoke the broad `doctl` token.
- **6 Frigg control node:** an ongoing hardening surface (credential-mirror sync, AppRole-expiry alerting).

**Not started or deferred**
- **8b** vm-operator migration (deferred until alerting via VMAlert or ServiceMonitor-emitting charts need it).
- **9** Vault Agent / VSO: a burst-cluster learning exercise only; asgard keeps ESO.
- Long-term: Vault → OpenBao (~12 months out).

**Dropped:** Phase 7 Jotunheim (no RAM, 2026-10-05) and Phase 5k package mirror (disk, 2026-10-09).

## Foundation

| Phase | Status | Outcome |
|-------|--------|---------|
| 1 Design · 2 UCG-Ultra · 3 Synology · 4 Proxmox cluster | ✅ | Design complete; VLANs, zones and firewall; Synology volumes, NFS and iSCSI target; Urd/Verd/Skuld clustered as `niflheim`. |
| 4c Verd hardware refresh | ✅ 2026-05-23 | Beelink N100/16GB → MSI Cubi i3-1215u/32GB by live-migrating off, SSD transplant and migrating back, with no workload downtime. The NIC rename (`nic0` → `enp45s0`) was fixed at the console and produced the reboot-test rule. |
| 5a PBS | ✅ | LXC 1101. Moved Skuld → Urd 2026-10-01 (10g1 prerequisite; the NFS datastore is mounted inside the LXC, `pct migrate --restart`). |
| 5d KPN DMZ | ✅ | DMZ → UCG-Ultra WAN, IPv4 + IPv6. |

## Asgard K3s core

| Phase | Status | Outcome |
|-------|--------|---------|
| 5c Asgard K3s | ✅ | VMs, K3s, Flux, Sealed Secrets, Synology CSI, Vault, ESO, MetalLB, tigera-operator. A full teardown and rebuild was validated 2026-05-17 ([incident log](../incidents/README.md)). |
| 4a CP taint | ✅ 2026-05-21 | `control-plane:NoSchedule` on all three CPs, workloads moved to workers, Vault spread across workers. Surfaced two outages and ~10 findings ([incident log](../incidents/README.md)). |
| 4b Göndul Verd → Urd | ✅ 2026-05-22 | Recreated on Urd, stale etcd member cleared, rejoined as a server with `-e k3s_init_node=hlokk`. CP topology is now Göndul/Urd, Hlökk/Verd, Sigrún/Skuld; Vault stayed 3/3. |
| Worker rebuild (einherjar-urd) | ✅ 2026-05-22 | Corrected stale `template_node` references and doubled as validation of the worker-rebuild path. Vault ran 2/3 voters for ~25 min by design. |
| Storage tiering redesign | ✅ 2026-05-30 | Escaped the DS223J ~10-LUN cap: NFS (`csi-driver-nfs`) for file-class, `local-path` on a 50G per-worker xfs disk for replicated state (Vault Raft moved there), iSCSI only for block-critical DBs, emptyDir for caches. Runbook [`synology-storage-redesign.md`](../procedures/synology-storage-redesign.md), [incident](../incidents/2026-05-30-storage-redesign-nas-rework.md). |

## Edge, DNS and identity

| Phase | Status | Outcome |
|-------|--------|---------|
| 5b AdGuard Home · 5b.2 AdGuard IaC | ✅ / ✅ 2026-05-25 | Saga/Mimir/Kvasir with a keepalived VIP and sync; 5b.2 migrated them from manual to IaC (TF import of the LXCs, `adguard`/`adguardhome-sync`/`keepalived` roles), failover validated. Also rotated a leaked Proxmox API token. |
| 5e Authentik + Redis | ✅ 2026-05-17 | First deployed as a bare LoadBalancer; now behind Traefik with TLS. |
| 5e.1 Traefik + Gateway API + cert-manager | ✅ 2026-05-22 | Gateway API v1.5.1 CRDs, cert-manager v1.19.0, Traefik v40.2.0; wildcard `*.niflheim` via Let's Encrypt DNS-01 with a zone-scoped Cloudflare token. Authentik at `authentik.niflheim.xiiisins.com`. |
| 5e.2 Cloudflared + apex zone + WebFinger | ✅ 2026-05-23 | Locally-managed tunnel `asgard-k3s` (3 replicas), `midgard` and apex wildcards, second Gateway, WebFinger via a Caddy pod; Authentik now at `authentik.xiiisins.com`. |
| 5e.3 Tailscale OIDC + LXCs | ✅ 2026-05-23 | Authentik OIDC provider, `terraform/tailscale/` ACL module, LXCs 1113/1114/1115 (Bifrost/Heimdall/Gjallarbru) and the Munin subnet router; split auth (servers on TF-minted keys, devices via OIDC). |
| 5e.4 Tailnet DNS | ✅ 2026-05-24 | MagicDNS plus split DNS for `niflheim` and `midgard` to the AdGuard VIP; apex deliberately not split. |

## Data and platform services

| Phase | Status | Outcome |
|-------|--------|---------|
| 5f Factorio LXC | ✅ 2026-05-16 | Terraform + Ansible end to end; SFTP-only operator model. |
| 5g PostgreSQL + Teamspeak | ✅ 2026-05-25 | Fulla standalone, then HA; Teamspeak pivoted from an LXC to asgard K3s on Patroni, shared MetalLB VIP, SRV failover to `do-ts3`. Retro [teamspeak-k3s](../incidents/2026-05-25-teamspeak-k3s.md). |
| 5g.2 PG HA (Patroni, etcd-on-HAProxy) | ✅ 2026-05-24 | Cluster `niflheim-pg` (Fulla/Vör/Idunn), etcd on the HAProxy trio, VIP `10.0.10.210`, deterministic leader routing; Authentik cut over, failover validated. Generic `haproxy` and `keepalived` roles. Retro [haproxy-keepalived-vip](../incidents/2026-05-24-5g2-haproxy-keepalived-vip.md). |
| 5i NetBox IPAM/DCIM | ✅ 2026-05-24 | NetBox 4.6.1 in asgard (external PG, OIDC, internal-only). Surfaced the pod-to-MetalLB-VIP class and its CoreDNS rewrite fix. Retro [5i-netbox](../incidents/2026-05-24-5i-netbox.md). |
| 5i.3 TF → NetBox pattern | ✅ 2026-05-24 | `terraform/netbox/` (~160 resources) retrofits the hand-imported records; admin-token auth after provider incompatibilities. Retro [5i3](../incidents/2026-05-24-5i3-tf-netbox-retrofit.md). |
| 5h.2 Notifications (Hermod) | ✅ 2026-05-26 | AppriseAPI on LXC 1103 with four Discord channels by tag (Hrist critical, Mist alert, Ölrún media, Hel quarantine); Zabbix and Patroni producers wired; live Patroni switchover validated (and the 4.x `master`→`primary` rename caught). Design [`notifications.md`](../services/notifications.md). |
| 5h.3 Semaphore + drift-check | ✅ 2026-05-27 | Semaphore on K3s with NetBox dynamic inventory; drift-check every 6 h with a zero-`changed` baseline; custom GHCR image (2026-06-10); PVE host patching playbook (2026-06-18). Retro [5h3](../incidents/2026-05-27-5h3-semaphore-drift-check.md). |

## Applications

| Phase | Status | Outcome |
|-------|--------|---------|
| 5j Outline + Garage | ✅ 2026-05-26 | Garage v2.3.0 single-node S3 and Outline 1.8.0 in K3s on Patroni, OIDC via Authentik; 16 findings ([incident log](../incidents/2026-05-26-outline-garage-deploy.md)). |
| Startpage | ✅ 2026-05-31 | `home.xiiisins.com`, Caddy serving a whitelist-copy of the private startpage repo. Update: push, then `kubectl rollout restart deployment/startpage -n startpage`. [Service doc](../services/startpage.md). |
| MicroBin | ✅ 2026-05-31 | `paste.xiiisins.com`; NFS-backed JSON DB, partial Authentik ForwardAuth (`/list`, `/admin`). [Service doc](../services/microbin.md). |
| n8n (asgard) | ✅ → 🗑 removed 2026-10-03 | Built 2026-06-01, removed when it proved unused; the AIOps agent runs a separate n8n on Gná (10d). |
| Immich | ✅ 2026-09-03 | NFS library, Patroni with pgvector, Authentik OIDC; secondary copy only. [Service doc](../services/immich.md). |
| Overview page | ✅ 2026-10-06 | Internal status page and app launcher at `overview.niflheim.xiiisins.com` (also `status.xiiisins.com`), with a read-only same-origin query path to VictoriaMetrics. [Service doc](../services/overview.md). |
| 5h Jellyfin + media automation | 🟡 | Jellyfin LXC 1123 on Urd (QuickSync) built 2026-10-06; Sonarr + SABnzbd + Recyclarr in K3s built 2026-10-08 (23 of 24 episodes of the first series imported). Open items are listed under "What's left". [Jellyfin](../services/jellyfin.md), [media automation](../services/media-automation.md), [plan](../plans/active/5h-jellyfin.md). |

## Observability

| Phase | Status | Outcome |
|-------|--------|---------|
| 8 Observability (umbrella) | 🟡 | 8a and 8c done, 8b deferred. Wave S4 added the `infra-health-check` active prober (certs, tokens, Patroni/etcd quorum, PBS → Hermod every 12 h); see [1.0-stabilization.md](../plans/done/1.0-stabilization.md). |
| 8a VictoriaLogs + VictoriaMetrics | ✅ 2026-05-25 | In the `monitoring` namespace (no Grafana: vmui and the VL UI), `vlagent` shipping from 23 hosts, vmagent + kube-state-metrics, four vmui dashboards. Retro [phase-7-observability](../incidents/2026-05-24-phase-7-observability.md). |
| 8b vm-operator migration | 🔲 deferred | Move vlsingle/vmsingle from raw Helm to vm-operator CRDs when ServiceMonitor-emitting charts land or VMAlert is needed. |
| 8c Zabbix (host/LXC layer) | ✅ 2026-05-26 | Zabbix 7.0 LTS on LXC 1102 (Hugin, Urd) with Authentik SAML, fronted by Traefik with a direct backdoor. Retros [server](../incidents/2026-05-25-zabbix-server-deploy.md), [SAML](../incidents/2026-05-26-zabbix-saml-deploy.md); design [`zabbix.md`](../services/zabbix.md). |

## Secrets, control node and CI

| Phase | Status | Outcome |
|-------|--------|---------|
| 6 Vault OIDC + Frigg control node | 🟡 | Stage 1 (Vault UI behind Authentik OIDC) done 2026-05-31; Vault TLS via the internal CA 2026-06-20. Stage 2: Frigg (HA VM 2900) runs the full TF/Ansible/Flux cycle with its own AppRole and hosts `claude remote-control`; its fleet SSH key lives only in a memory-only agent (2026-10-02), and RC login invalidation self-heals through `frigg-reauth-listener` (2026-09-22). Held at 🟡 as an ongoing hardening surface: [`frigg-control-node.md`](../known-issues/frigg-control-node.md). |
| CI gate + `main` ruleset | ✅ 2026-10-01 | One required `CI gate`, a `main` ruleset in `terraform/github/`, auto-merge. Open: fine-grained PAT into 1P/shim, ansible-lint baseline burn-down, Galaxy cache. [Procedure](../procedures/ci.md). |
| 9 Secrets runtime retrieval | 🔲 learning only | Vault Agent / VSO tried on the ephemeral burst cluster ([procedure](../procedures/k8s-burst-test.md)); **not** a production migration, asgard keeps ESO (2026-10-09; see [`decisions.md`](decisions.md), "Phase 9 scoped to the burst cluster"). |

## Phase 10: AIOps and self-healing

Plan: [`aiops-roadmap.md`](../plans/active/aiops-roadmap.md) (planned 2026-10-01; live state checked 2026-10-05). Closes detection → action in tiered stages (T0–T3 by blast radius, an action registry, a kill switch first). Build findings: [`phase-10-build-findings.md`](../incidents/2026-10-01-phase-10-build-findings.md).

| Phase | Status | Outcome |
|-------|--------|---------|
| 10a Offsite node `do1` | ✅ built and cut over 2026-10-01; legacy resources destroyed 2026-10-10 | Unmanaged droplet rebuilt from Terraform + Ansible (TS3 failover, HeyLeaf PlantNet proxy, Gatus watcher), reserved IP, scoped Tailscale grants. After the soak the legacy droplet, `do-tailscale-p01`, stale firewalls/keys/registry and the pre-hardening snapshot were destroyed and `do1-next` retired; do1 serves and is probed on `do1.xiiisins.com`. Left: stale Tailscale machine records, revoke the broad `doctl` token. [Procedure](../procedures/offsite-do1.md). |
| 10b Test substrate + outside watcher | ✅ 2026-10-01/02 | **10b1** canary pool (LXCs 1190–1192, `site-nonprod.yml`, alerts capped at the `info` tier). **10b2** burst substrate on DigitalOcean (`terraform/digitalocean-burst/`, reaper on Frigg; smoke-tested for ~$0.11). **10b3** Gatus on `do1` with a Frigg heartbeat and an independent Discord channel. Procedures: [canary-pool](../procedures/canary-pool.md), [burst-substrate](../procedures/burst-substrate.md), [offsite-watcher](../procedures/offsite-watcher.md). |
| 10c Machine-readable ops | ✅ 2026-10-01 | Alert schema + normalizer, 20 runbooks with stable `runbook_id`s, an action registry (4 T0 + 4 T1 actions, every mutator approval-capped) and 8 playbooks + 7 Semaphore templates; linted in CI. [`aiops/README.md`](../../aiops/README.md). |
| 10d Diagnosis chat-ops | ✅ live 2026-10-02/03 | n8n on Gná (LXC 1121) receives context-rich Zabbix events beside Hermod; a write-less Toolbelt API on Frigg (19 read-only tools) grounds the agent's `diagnosis.v1` before a Discord thread is posted; replay acceptance incl. the Skuld freeze and an injection control. [Plan](../plans/done/10d-diagnosis-chatops.md), [retro](../incidents/2026-10-03-10d2-10d3-toolbelt-agent.md). |
| 10e Approval-gated actions + chat agent | ✅ live 2026-10-03 | Action engine with params-hash-bound approvals, kill switch and expiry; the Discord bot Ratatoskr (LXC 1122) is the only approver; the executor runs as a Task Runner in a dedicated Semaphore `aiops` project. Left: NetBox journal entries (10e2) and a heartbeat check. [Plan](../plans/done/10e-approval-actions.md), [procedure](../procedures/aiops-actions.md). |
| 10f Autonomous T1 healing | 🟡 deployed 2026-10-03; soak running | Registry `autonomy:` scope (canaries only), master switch, breaker, rate limits and one enabled policy (`restart-failed-unit`). Scheduled fault injection every 8 h (added 2026-10-05) produced the first clean heal in ~5 min 48 s. Read-out ~2026-10-17. [Plan](../plans/active/10f-autonomous-healing.md), [procedure](../procedures/aiops-autonomy.md). |
| 10g Fleet rebuild loop | 🟡 stage A proven 2026-10-04 | Approval-gated canary rebuild works (117 s on the clean run; five earlier runs failed during bring-up). Autonomous canary rebuild stays off; replica and worker stages not built. Prerequisites met: offsite restore drill, PBS off Skuld; PBS capacity dropped as a prerequisite (67% on 2026-10-10). [Plan](../plans/active/10g-rebuild-loop.md). |
| 10h Predictive changes + agent-authored PRs | 🟡 all parts live; exit criteria unmet | 10h1 forecasts in quiet shadow mode, 10h2 PR author (classes `docs`, `drift-note`, `drift`, `k8s`, `capacity`; a canary test for infra PRs; a burst-cluster test for `k8s/` PRs), 10h3 automatic incident write-ups. By 2026-10-05: 13 change requests, 6 merged PRs. [Plan](../plans/active/10h-predictive-change.md), [burst test](../procedures/k8s-burst-test.md). |
| 10i Pod rightsizing (VPA recommend-only) | 🟡 10i0, 10i0b, 10i1 live 2026-10-05 | NetBox/Authentik trimmed; every Flux-managed pod sized from 30 days of VictoriaMetrics history; 2 GiB per worker reserved for the OS and K3s; VPA recommender with `updateMode: "Off"`. 10i2 (Toolbelt data) → 10i3 (Gná digest) → 10i5 (72 h watch) → 10i4 (resources-only PRs) remain. [Plan](../plans/active/10i-rightsizing.md). |

## Dropped and deferred

| Phase | Status | Why |
|-------|--------|-----|
| ~~5k Package mirror (Hvergelmir)~~ | ✖ dropped 2026-10-09 | A full Debian + Rocky mirror with snapshots costs more disk than the homelab has to spare (PBS at 81%). Upstream deletion is covered by pinned versions and release-page `.deb`s; revisit only if we build our own packages. Shelved design in [`open-questions.md`](open-questions.md). |
| ~~7 Jotunheim K3s~~ | ✖ dropped 2026-10-05 | No capacity: six more VMs (~60 GB RAM) against ~6–8 GB free per node. Throwaway experiments move to the ephemeral burst cluster. VLANs 30/31, VMIDs 3001–3999 and the Jotunheim names stay reserved; [`jotunheim-k3s.md`](../services/jotunheim-k3s.md) is the shelved design. |
