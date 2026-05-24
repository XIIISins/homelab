<!-- docs/homelab-design.md -->

# Homelab design — index

*Last updated: 2026-05-24 — restructured into targeted docs.*

This document is the entry point. Each section below points at the focused doc for that concern. Everything that used to live in this file (architecture, services, decisions, incident retrospectives, etc.) is preserved verbatim in `docs/` — just split for targeted loading.

---

## Purpose

A ground-up homelab rebuild demonstrating senior-level infrastructure design.

### Goals
- Provide reliable services to friends and family (Teamspeak, Factorio, Immich)
- Learn and demonstrate modern infrastructure concepts (Kubernetes, GitOps, IaC, network segmentation)
- Portfolio/resume project at senior/principal infrastructure level

### Principles
- Everything defined as code before it exists in production
- Complexity only where it serves a purpose
- Self-healing at every layer
- Full audit trail — every change traceable to a human or automated system

---

## Document map

### Architecture — the design itself
- [`architecture/hardware.md`](architecture/hardware.md) — physical nodes (Urd/Verd/Skuld + Munin), storage tier per node, migration history, naming convention, network hardware, physical topology.
- [`architecture/network.md`](architecture/network.md) — VLAN table, IP assignments (per-LXC + per-VM), cluster CIDRs, resource ID scheme, LXC ID grouping, three-zone DNS scheme + TLS strategy, internet exposure (KPN DMZ → UCG-Ultra) + firewall posture.
- [`architecture/identity-secrets.md`](architecture/identity-secrets.md) — identity model (Authentik + LDAP + SSSD + break-glass), full secrets architecture (1Password / HashiCorp Vault / Ansible Vault), AppRole bootstrap runbook, control-node fish tooling, OpenBao migration plan.
- [`architecture/iac.md`](architecture/iac.md) — tool layering: Terraform (API'd resources) / Ansible (OS-level) / Flux (in-cluster) / docs (no-API infra).

### Services — what runs where
- [`services/synology.md`](services/synology.md) — Munin NAS volumes, NFS shares, iSCSI target convention, OOB via Tailscale.
- [`services/asgard-k3s.md`](services/asgard-k3s.md) — Asgard K3s cluster: core infrastructure table, automation, core services, VMs, multi-homed workers, K3s install role, Calico CNI addon, Flux Kustomization structure, HelmRelease pins.
- [`services/jotunheim-k3s.md`](services/jotunheim-k3s.md) — Jotunheim K3s cluster: non-critical workloads, planned VMs, planned services.
- [`services/asgard-lxcs.md`](services/asgard-lxcs.md) — full asgard LXC table (PBS, Zabbix, AdGuard, Tailscale, Factorio, Teamspeak, PostgreSQL, HAProxy, Jellyfin) + build-order revision.
- [`services/factorio.md`](services/factorio.md) — Factorio LXC architecture (operator self-service via SFTPGo + reconcile loop). Template pattern for operator-managed services.
- [`services/postgres.md`](services/postgres.md) — PostgreSQL LXC architecture (Fulla deployed 2026-05-17): PG 17 + TLS + scram-sha-256, management-role split, per-service DB provisioning, cluster build sequence.

### Operations — what's been done, what's decided, what's open
- [`operations/build-sequence.md`](operations/build-sequence.md) — phase status table (Phases 1-8). Concise one-line rows with ✅/🟡/🔲 tick.
- [`operations/decisions.md`](operations/decisions.md) — Key decisions log, ~130 rows. Every architectural decision with reason + date.
- [`operations/open-questions.md`](operations/open-questions.md) — pending tasks + open architectural questions.

### Incidents — what broke and what we learned
- [`incidents/`](incidents/) — per-incident retrospectives. See [`incidents/README.md`](incidents/README.md) for the date-indexed table.

### Procedures — operational runbooks
- [`procedures/teardown-rebuild.md`](procedures/teardown-rebuild.md) — full cluster teardown + rebuild runbook (validated 2026-05-17). Multi-day sequence.

### Known issues — Claude-runtime gotchas
- [`../CLAUDE.md`](../CLAUDE.md) "Known gotchas" — currently canonical home. Migration to [`known-issues/`](known-issues/README.md) planned (see README there).

---

## How to navigate

- **Working on a specific service?** Load [`services/<service>.md`](services/) + any relevant [`incidents/*.md`](incidents/) that touched it.
- **Reviewing an architectural choice?** [`operations/decisions.md`](operations/decisions.md) is the canonical table.
- **Investigating a past failure?** [`incidents/README.md`](incidents/README.md) → relevant entry.
- **Planning new work?** Follow the pre-flight checklist in [`../CLAUDE.md`](../CLAUDE.md): scan `docs/` for X, check [`operations/open-questions.md`](operations/open-questions.md) for prereqs, check Known gotchas, then draft sequence with prerequisites first.
- **Building a fresh node / cluster from scratch?** Start with [`procedures/teardown-rebuild.md`](procedures/teardown-rebuild.md).

---

*This document is a living index. Update the map above when documents are added or restructured.*
