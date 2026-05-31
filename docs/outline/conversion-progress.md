<!-- docs/outline/conversion-progress.md -->

# Outline conversion — progress

Working tracker for converting `docs/` content into reader-shaped Outline pages. Not itself a wiki page — this file stays in the repo as the meta record of what's been authored, what's pending, and the conventions established along the way.

*Last updated: 2026-05-29.*

---

## Status snapshot

- **Drafted:** 35 pages — all planned sections complete (Components & Interactions, Hardware, Services and purpose, Procedures, Troubleshooting) plus the standalone URLs page.
- **Pending:** none. Remaining work is review, the publish step (Outline import + link rewriting), and per-service build-out as new services land.

---

## Done

### Homelab collection

- [x] `overview.md` — collection root. Goals, scope, design principles, at-a-glance table, topology, invariants, secrets model, DNS+TLS, reliability and change, phase posture, reader paths.

### Components & Interactions

- [x] `components-and-interactions.md` — section parent. 8-layer stack, 27-row components table, 8 canonical interaction flows.
- [x] `components-and-interactions/compute-and-hypervisors.md` — Proxmox cluster, VM/LXC decision, asgard/jotunheim split, ID scheme, specs, bootstrap flow.
- [x] `components-and-interactions/network.md` — VLAN model, firewall posture, DNS architecture (incl. in-cluster CoreDNS rewrites), MetalLB, multi-homed worker landmines, source-based policy routing, key IPs.
- [x] `components-and-interactions/storage-and-data.md` — local LVM-thin, Synology iSCSI, Synology NFS, Garage S3, Postgres + Patroni, PBS, failure surfaces.
- [x] `components-and-interactions/identity-and-secrets.md` — Authentik OIDC/LDAP/SAML, three-store model, Vault Kubernetes + AppRole auth, ESO + AppRole interaction flows, failure surfaces.
- [x] `components-and-interactions/gitops-and-automation.md` — Terraform/Ansible/Flux/docs ownership boundary, Flux per-component-config layering, Ansible per-host-group playbooks + NetBox dynamic inventory, Semaphore drift loop, end-to-end change flow, failure surfaces.
- [x] `components-and-interactions/edge.md` — three DNS zones + three Gateways + three wildcards (one CF token), Traefik with Gateway API only, cert-manager DNS-01, Cloudflared external entry, in-cluster CoreDNS rewrite, three access paths end-to-end, failure surfaces.
- [x] `components-and-interactions/observability.md` — VM/VL in-cluster vs Zabbix LXC split, vmagent (K8s metrics + apiserver-proxy scrape), vlagent (DaemonSet + Ansible systemd, two ingress endpoints), Zabbix host+service templates, Hermod tag taxonomy + producer wiring, failure surfaces.

### Hardware

- [x] `hardware.md` — section parent. Physical-layer overview, full kit table, cabling topology, link layer, storage-tier teaser.
- [x] `hardware/hypervisors.md` — the three Norn nodes, per-node CPU/RAM/NVMe, etcd storage-tier ranking (DRAM-less Gen 4 vs DRAM Gen 3), disk layout, QuickSync.
- [x] `hardware/networking.md` — UCG-Ultra, dumb switches, KPN DMZ boundary, 1 GbE link layer, out-of-band Tailscale on Munin.
- [x] `hardware/storage.md` — Synology DS223J (drives, RAID, iSCSI LUN cap), per-node NVMe specs, storage-backend split.

---

### Services and purpose

- [x] `services-and-purpose.md` — section parent. Catalog grouped by audience (friends & family / operator tooling / platform) + planned list.
- [x] `services-and-purpose/jellyfin.md` — media server, QuickSync, privileged-LXC rationale (status: planned).
- [x] `services-and-purpose/teamspeak.md` — voice server, shared MetalLB VIP, SRV failover ring.
- [x] `services-and-purpose/factorio.md` — game server, SFTP self-service + reconcile-loop pattern.
- [x] `services-and-purpose/outline.md` — the wiki itself; Postgres + Garage + Redis + Authentik wiring.
- [x] `services-and-purpose/netbox.md` — IPAM/DCIM truth model + Terraform standing pattern.
- [x] `services-and-purpose/semaphore.md` — Ansible scheduler, the four templates, drift-check baseline.
- [x] `services-and-purpose/zabbix.md` — host monitoring in its own failure domain, SAML auth.
- [x] `services-and-purpose/hermod.md` — notification hub, Discord tag taxonomy, producer wiring.

Platform services (Authentik, Postgres, Garage, Munin, AdGuard, PBS, Vault) are catalog rows on the parent that cross-reference their deep pages in **Components & interactions** / **Hardware** — deliberately not duplicated as subpages.

### Procedures

- [x] `procedures.md` — section parent. Runbook philosophy + index ordered by blast radius.
- [x] `procedures/approle-rotation.md` — the two-AppRoles / two-helpers footgun + hash-verify.
- [x] `procedures/vault-recovery.md` — Raft rejoin, stuck-init recovery, token re-mint, step-down.
- [x] `procedures/k3s-node-rebuild.md` — CP + worker rebuild (delete-node-first, iSCSI cleanup).
- [x] `procedures/netbox-initial-data-import.md` — dependency-ordered seed (bootstrap/DR artifact).
- [x] `procedures/teardown-rebuild.md` — whole-cluster DR rebuild; sealed-secrets-key danger.

### Troubleshooting

- [x] `troubleshooting.md` — section parent. Symptom-first triage, first-three-things, index by area.
- [x] `troubleshooting/storage-and-iscsi.md` — mount failures, orphan sessions, LUN cap, RO fsck, CSI down.
- [x] `troubleshooting/dns-and-networking.md` — internal NXDOMAIN, VIP reachability, tromboning, AGH sync, multi-homed drops.
- [x] `troubleshooting/kubernetes-and-flux.md` — duplicate node, stuck rollout, Helm reconcile, ESO sync, immutable Job, casing.
- [x] `troubleshooting/vault-and-postgres.md` — sealed/token expiry, AppRole mismatch, sslmode reject, write-to-replica, forced failover.
- [x] `troubleshooting/identity-and-edge.md` — outpost host caching, per-app group deny, CF token stale, SSH hostkey mismatch, OIDC discovery 404.

### Standalone

- [x] `urls.md` — service directory: every hostname, what it is, how to log in, internal vs external.

---

## Shape conventions established

Captured during the first few pages. Apply to anything new.

- **File path header.** Every file starts with an HTML comment containing its repo-relative path (`<!-- docs/outline/<path>.md -->`). Same convention as the rest of the repo; lets `grep` find moved files.
- **Section = page with subpages.** Section parent is `docs/outline/<section>.md` (not `<section>/overview.md`). Subpages live in a sibling directory `docs/outline/<section>/`.
- **Kebab-case file and directory names.** Display titles ("Services and purpose") get rewritten by the publish step; repo paths stay shell-friendly.
- **Length norm.** Collection overview ~175 lines. Section parents ~225 lines. Subpages ~150–220 lines, scaling with concrete content (tables, multi-step flows). Long pages are fine when complexity warrants; pad isn't.
- **No historical guardrails.** Wiki pages state current truth. "X, not Y as earlier drafts had it"-style content stays in CLAUDE.md gotchas / incidents / decisions. (See memory `feedback_outline_no_historical_guardrails.md`.)
- **Cross-references use the canonical name in bold** (`**Hardware** section`, `**Edge** (this section)`). Publish step rewrites to Outline doc IDs.
- **See-also block at the end of every subpage** pointing at adjacent siblings + the relevant other-section pages.
- **"Failure surfaces" pattern.** Pages where failure feels concrete (storage today, possibly edge + identity later) end with a section walking through what happens when each component fails. Compute and Network didn't need this; storage did.

---

## Layer order (parent-page contract)

The Components & Interactions parent lists eight layers in this order. Subpage order and "Where to go deeper" hints follow this.

1. Physical
2. Network
3. Compute
4. Storage & data
5. Identity & secrets
6. Orchestration
7. Edge
8. Observability

If the order shifts again, update the parent + this file + the "See also" references.

---

## Next

All planned sections are drafted. What remains:

- **Review pass** — read the full set end-to-end for voice consistency, dead cross-references, and any current-truth drift.
- **Publish step** — import into Outline and rewrite the bold canonical-name cross-references (`**Hardware** section`, etc.) to real Outline doc IDs.
- **Ongoing build-out** — add a per-service subpage when a new service lands (Immich, n8n, Privatebin, Startpage); add troubleshooting playbooks as new failure modes surface. The structure is in place; these are append operations.
