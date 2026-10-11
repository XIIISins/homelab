<!-- docs/outline/conversion-progress.md -->

# Outline conversion — progress

Working tracker for the reader-shaped Outline pages built from `docs/`. Not itself a wiki page: it stays in the repo as the record of what exists, what it was built from, and the conventions to keep. See `README.md` for the sync tooling.

*Last updated: 2026-10-11.*

---

## Status snapshot

- **Pages:** 51, all rewritten from scratch on 2026-10-11 against the as-built canonical docs (`docs/architecture/`, `docs/services/`, `docs/procedures/`, `docs/known-issues/`, `docs/operations/`) and the IaC. The previous tree (last drift-reviewed 2026-06-18) was deleted, not patched, so nothing carries over from it; its content and the old doc IDs are in Git history (`.outline-manifest.json` at the commit before this rewrite).
- **Not yet published.** `.outline-manifest.json` was removed with the old pages. The next `sync_outline.py --apply` has no manifest, so it matches live pages by title and updates them in place. If the live `Homelab` collection should be rebuilt rather than updated (for the link-rendering gotcha in `README.md`), follow the whole-collection recreate in `README.md`: soft-delete the old managed docs first (IDs from the old manifest in Git history), then run `--apply` twice. Both are outward-facing writes; run deliberately.
- **Changes from the previous set:**
  - Added: Immich, Media automation, Overview page, AIOps agent (services); Everyday operations, Landing a change, Postgres operations, Secret mirroring, K3s & Calico upgrade, Backups & restore drills (procedures); Hosts & Proxmox, Monitoring & automation, Applications (troubleshooting).
  - **Procedures and Troubleshooting are full runbooks** (second pass, 2026-10-11): each carries copy-paste commands an on-call person can run without having built the system, taken from `docs/procedures/` and `docs/known-issues/` and the IaC (unit names, namespaces, IPs, Vault paths, helper functions). Bash unless a block says fish; the rotation blocks are fish because that is the proven form.
  - Removed: the in-cluster n8n page (that service is gone; "n8n" now means the AIOps agent, covered by AIOps agent) and the NetBox initial-import runbook (executed and archived).
  - Reframed throughout: one cluster (asgard), tiered storage, Authentik/Vault/1Password/Ansible Vault model, Semaphore plus the AIOps tier, Jellyfin on an LXC.
- **Known deliberate gaps:** LDAP-backed SSH is described as designed but not deployed; the live state of the repository's open items (build sequence "What's left") is summarised in one paragraph on the overview, not enumerated.

---

## Pages

### Homelab collection
- `overview.md`: goals, scope, principles, at-a-glance, topology, invariants, secrets model, DNS+TLS, reliability and change, posture, reader paths.
- `urls.md`: hostname directory with sign-in method and reach.

### Components & interactions
- `components-and-interactions.md`: eight layers, component table, eight canonical flows.
- `.../compute-and-hypervisors.md`, `network.md`, `storage-and-data.md`, `identity-and-secrets.md`, `gitops-and-automation.md`, `edge.md`, `observability.md`.

### Hardware
- `hardware.md`, `hardware/hypervisors.md`, `hardware/networking.md`, `hardware/storage.md`.

### Services and purpose
- `services-and-purpose.md`: catalog by audience, tiers of care.
- Subpages: `jellyfin`, `media-automation`, `teamspeak`, `factorio`, `immich`, `outline`, `microbin`, `startpage`, `overview`, `netbox`, `semaphore`, `zabbix`, `hermod`, `aiops-agent`.
- Platform services (Authentik, Vault, Postgres, Garage, AdGuard, Munin, PBS, Traefik, Tailscale, Frigg) are catalog rows with a pointer to their deep page, deliberately not duplicated as subpages.

### Procedures
- `procedures.md`: session setup (credentials, SSH, where to run what), rules, escalation list, index by blast radius, order of attack.
- Subpages: `everyday-operations`, `landing-a-change`, `postgres-operations`, `secret-mirroring`, `credential-rotation`, `approle-rotation`, `backups-and-restore`, `k3s-upgrade`, `k3s-node-rebuild`, `proxmox-host-patching`, `vault-recovery`, `teardown-rebuild`.
- Each: when to use, prerequisites, numbered steps with commands, verify, rollback.

### Troubleshooting
- `troubleshooting.md`: a one-block read-only health sweep, index by symptom, index by alert (`RB-*` ids), first-response habits.
- Subpages: `storage-and-iscsi`, `dns-and-networking`, `kubernetes-and-flux`, `vault-and-postgres`, `identity-and-edge`, `hosts-and-proxmox`, `monitoring-and-automation`, `applications`. Each: first diagnostics (read-only commands), then symptom → cause → fix tables with commands.

---

## Shape conventions

- **File path header.** Every file starts with `<!-- docs/outline/<path>.md -->`.
- **Section = page with subpages.** The parent is `docs/outline/<section>.md`; subpages live in `docs/outline/<section>/`.
- **Kebab-case paths**; the H1 is the wiki title and what the sync matches on, so keep titles unique.
- **Length.** Overview ~175 lines, section parents up to ~225, subpages up to ~220, service pages short. Runbook pages (Procedures, Troubleshooting) run longer because they carry the commands.
- **Current truth only.** No "X, not Y as earlier drafts had it"; that lives in `decisions.md`, `incidents/` and `known-issues/`.
- **Cross-references use the canonical name in bold.** A "See also" block closes every subpage. The sync turns bold names into links (see `README.md`).
- **Failure surfaces** sections where failure behaviour is concrete.
- **No secret values.** Identifiers (hosts, IPs, Vault paths) are fine; the repo is public.

## Layer order (parent-page contract)

Physical, Network, Compute, Storage & data, Identity & secrets, Orchestration, Edge, Observability. If the order shifts, update the parent page, this file and the "See also" references.

---

## Keeping it current

The wiki is a reader-shaped view; the canonical docs remain the source. When a canonical doc changes materially (a service moves, a tier changes, a procedure is archived), update the matching page here and re-run the sync. Per-page sources:

| Area | Built from |
|---|---|
| Overview, Hardware | `CLAUDE.md`, `docs/architecture/hardware.md`, `docs/operations/build-sequence.md` |
| Network, Edge | `docs/architecture/network.md`, `terraform/adguard/rewrites.tf`, `k8s/asgard/` gateway, cert and tunnel config |
| Storage & data | `docs/procedures/storage-tiering.md`, `docs/services/{postgres,garage,synology}.md`, `docs/procedures/{offsite-backups,pbs-restore-test}.md` |
| Identity & secrets | `docs/architecture/identity-secrets.md`, `docs/known-issues/{vault,authentik}.md` |
| GitOps & automation | `docs/architecture/{iac,ansible-orchestration}.md`, `docs/procedures/ci.md`, `docs/operations/build-sequence.md` (Phase 10) |
| Observability | `docs/services/{observability,zabbix,notifications}.md` |
| Services | `docs/services/*.md` |
| Procedures, Troubleshooting | `docs/procedures/*.md`, `docs/known-issues/*.md` |
