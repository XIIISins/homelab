<!-- docs/outline/conversion-progress.md -->

# Outline conversion — progress

Working tracker for converting `docs/` content into reader-shaped Outline pages. Not itself a wiki page — this file stays in the repo as the meta record of what's been authored, what's pending, and the conventions established along the way.

*Last updated: 2026-06-18.*

---

## Status snapshot

- **Drafted:** 35 pages — all planned sections complete (Components & Interactions, Hardware, Services and purpose, Procedures, Troubleshooting) plus the standalone URLs page.
- **Drift review (2026-06-18):** all 35 pages re-checked against current `main`. Fixed: worker VM specs (16 GB / 30 GB + `/data`), Phase-6/Frigg posture (overview + identity), secrets-rule header + public-repo posture + vault-backed shim (identity), private→public repo (gitops), three now-live services (Startpage / MicroBin / n8n) added to URLs + services catalog, storage troubleshooting lede + LUN diagnostic, Vault-recovery + teardown iSCSI→local-path. Storage pages and the Hermod page were *not* changed — they're correct and actually ahead of the stale canonical docs (see below).
- **Publish tooling:** `sync_outline.py` reconciles `docs/outline/` → live `Homelab` collection. Dry-run against live (2026-06-18): **34 UPDATE + 1 CREATE (`overview.md`) + 0 SKIP** — every page already exists; the sync brings their drifted content current. `--apply` not yet run (awaiting go-ahead — it rewrites 34 live pages).
- **Pending:** run `--apply`; write the API token to Vault `secret/ansible/outline/api-token` (blocked on a write-capable token); cross-reference link rewriting; per-service subpages for Startpage/MicroBin/n8n.

> **Canonical-doc drift surfaced during review (out of scope for this branch — flag for a separate `docs(...)` commit on `main`):**
> - `CLAUDE.md` storage invariant still says "Synology CSI … iSCSI only", but `csi-driver-nfs` + `local-path` are live tiers and the iSCSI StorageClass is `synology-csi-iscsi-retain-vol2`. The wiki storage pages reflect the live tiered model.
> - `docs/services/notifications.md` says AppriseAPI runs under **uvicorn**; the live `hermod-api` role runs **gunicorn**. The wiki Hermod page (gunicorn) is correct.

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

All sections are drafted, published, and (2026-06-18) drift-reviewed. What remains:

- **Run the sync `--apply`** — pushes the drift-reviewed content to the 34 live pages and creates the one missing page (`overview.md`). See `README.md` for usage. Outward-facing write; run deliberately.
- **Write the API token to Vault** — `secret/ansible/outline/api-token` (field `token`). The 1P mirror (`[Asgard] - Mirror - Outline - admin API token`) exists; the Vault write needs a write-capable token (the cached AppRole was expired; root is bootstrap-only).
- **Cross-reference link rewriting** — the bold canonical-name placeholders (`**Hardware** section`, etc.) → real Outline `/doc/<id>` links. Separate reviewed pass once the manifest is populated; needs a curated name→doc alias map. Not automated by `sync_outline.py` by design.
- **Per-service subpages** for Startpage / MicroBin / n8n (now live) and Immich (when it lands). Parent catalog covers them today; these are append-only build-out.
