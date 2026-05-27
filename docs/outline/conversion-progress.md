<!-- docs/outline/conversion-progress.md -->

# Outline conversion — progress

Working tracker for converting `docs/` content into reader-shaped Outline pages. Not itself a wiki page — this file stays in the repo as the meta record of what's been authored, what's pending, and the conventions established along the way.

*Last updated: 2026-05-27.*

---

## Status snapshot

- **Drafted:** 6 pages (1 collection root + 1 section parent + 4 subpages).
- **Pending:** 3 subpages under Components & Interactions, then 4 more sections (Hardware, Services and purpose, Procedures, Troubleshooting) plus the URLs page.

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

---

## Pending

### Components & Interactions — remaining subpages

- [ ] `gitops-and-automation.md` — Flux structure, Semaphore templates, Ansible role layout, Terraform/Ansible/Flux layering, drift-check.
- [ ] `edge.md` — Traefik, Cloudflared, cert-manager DNS-01, the three DNS zones at the certificate layer.
- [ ] `observability.md` — VictoriaMetrics + VictoriaLogs, Zabbix split, Hermod tag taxonomy, vmagent/vlagent shippers.

### Hardware section

- [ ] `hardware.md` — section parent. Physical layer overview.
- [ ] `hardware/hypervisors.md` — physical Proxmox hosts (Urd / Verd / Skuld), CPU/RAM/NVMe per node, storage tier comparison.
- [ ] `hardware/networking.md` — UCG-Ultra, switches, 1 GbE link layer, cabling, physical topology.
- [ ] `hardware/storage.md` — Synology DS223J, drives, RAID layout, per-node NVMe.

### Services and purpose section

- [ ] `services-and-purpose.md` — section parent. Service catalog grouped by who it serves.
- [ ] Per-service subpages: Jellyfin, Teamspeak, Factorio, Outline, Authentik, NetBox, Vault, etc. (one per service; build out as the section matures).

### Procedures section

- [ ] `procedures.md` — section parent.
- [ ] Per-procedure subpages: teardown/rebuild, NetBox initial data import, AppRole rotation, etc. (source: `docs/procedures/`).

### Troubleshooting section

- [ ] `troubleshooting.md` — section parent.
- [ ] Per-issue subpages: iSCSI orphan sessions, DS223J LUN cap, DNS pollution from public resolvers, etc. (source: CLAUDE.md gotchas reshaped as reader playbooks).

### Standalone

- [ ] `urls.md` — service directory with login paths. Single page; becomes a section if it sprouts subpages later.

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

Resume at **`gitops-and-automation.md`** under Components & Interactions. Cover Flux's per-component-config layering, the Terraform/Ansible/Flux split (who provisions what), Semaphore's drift-check + apply templates, and the data-flow when a change lands in Git.
