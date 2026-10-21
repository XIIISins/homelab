<!-- docs/homelab-design.md -->

# Homelab design — index

Entry point to `docs/`. This page lists every doc area and the docs worth knowing by name; each directory's own index (or `ls`) is the complete list, so adding a doc there does not require touching this page. Current state and what's left: [`operations/build-sequence.md`](operations/build-sequence.md).

## Purpose

A ground-up homelab demonstrating senior-level infrastructure design.

- **Goals:** reliable services for friends and family (Teamspeak, Factorio, Immich, Jellyfin); a Kubernetes learning environment; a senior/principal-level infra portfolio.
- **Principles:** everything is code before it is production; complexity only where it serves a purpose; self-healing at every layer; every change traceable to a human or automated actor.

## Architecture — the design itself

- [`homelab-overview.png`](architecture/homelab-overview.png): one-page picture of the whole lab (edge, three Proxmox hosts and their guests, asgard K3s, storage, off-site), with no IPs or IDs so it is safe to reuse. Dark theme on a transparent background, like the service diagrams (light ink, so it reads best on a dark page). Editable source: [`homelab-overview.excalidraw`](architecture/homelab-overview.excalidraw) (open it at excalidraw.com); vector copy: [`homelab-overview.svg`](architecture/homelab-overview.svg).
- [`hardware.md`](architecture/hardware.md): nodes (Urd/Verd/Skuld, Munin), storage tiers, naming convention.
- [`network.md`](architecture/network.md): VLANs, per-LXC/VM IPs, cluster CIDRs, VMID scheme, three-zone DNS and TLS, internet exposure and firewall.
- [`identity-secrets.md`](architecture/identity-secrets.md): Authentik/LDAP/SSSD, the three secret stores, AppRole bootstrap, control-node tooling.
- [`iac.md`](architecture/iac.md): tool layering (Terraform / Ansible / Flux / docs for no-API infra).
- [`ansible-orchestration.md`](architecture/ansible-orchestration.md): playbook structure, Semaphore scheduling, NetBox dynamic inventory, the drift-check loop.

## Services — what runs where

- **Platform:** [`asgard-k3s.md`](services/asgard-k3s.md) (the cluster, Flux structure, platform components), [`asgard-lxcs.md`](services/asgard-lxcs.md) (the LXC table), [`synology.md`](services/synology.md) (Munin), [`postgres.md`](services/postgres.md) (Patroni + HAProxy), [`adguard.md`](services/adguard.md) (DNS), [`netbox.md`](services/netbox.md) (IPAM/DCIM), [`garage.md`](services/garage.md) (S3).
- **Monitoring and alerting:** [`observability.md`](services/observability.md) (VictoriaMetrics/Logs), [`zabbix.md`](services/zabbix.md) (Hugin), [`notifications.md`](services/notifications.md) (Hermod → Discord).
- **User-facing:** [`jellyfin.md`](services/jellyfin.md), [`media-automation.md`](services/media-automation.md), [`immich.md`](services/immich.md), [`outline.md`](services/outline.md), [`teamspeak.md`](services/teamspeak.md), [`factorio.md`](services/factorio.md) (the template for operator-managed services), [`startpage.md`](services/startpage.md), [`microbin.md`](services/microbin.md), [`overview.md`](services/overview.md), [`php-watch.md`](services/php-watch.md).
- **Diagrams:** every service page above opens with a hand-drawn sketch (Excalidraw source + SVG in [`services/diagrams/`](services/diagrams/README.md)); conventions and editing steps are in that README.
- **Shelved:** [`jotunheim-k3s.md`](services/jotunheim-k3s.md) (Phase 7, dropped 2026-10-05; design record only).

## Operations — state and decisions

- [`build-sequence.md`](operations/build-sequence.md): the roadmap with a "What's left" block; closing narratives in [`build-sequence-history.md`](operations/build-sequence-history.md).
- [`decisions.md`](operations/decisions.md): decision log by topic, each with reason and date; original wording in [`decisions-archive.md`](operations/decisions-archive.md).
- [`open-questions.md`](operations/open-questions.md): open work and prerequisites; closed items in [`open-questions-archive.md`](operations/open-questions-archive.md).
- [`operations/drift/`](operations/drift/): agent-written drift notes awaiting operator edit.
- [`plans/`](plans/README.md): build plans (`active/`, `done/`, `deferred/`); the index is the only place a plan's state is recorded.

## Incidents and known issues

- [`incidents/`](incidents/README.md): dated retrospectives, indexed by date.
- [`known-issues/`](known-issues/README.md): per-subject gotchas (rule, why, symptom, recovery). Read the matching file before working on its subject.

## Procedures — runbooks

- **Rebuild and recovery:** [`teardown-rebuild.md`](procedures/teardown-rebuild.md) (full DR, dependency-ordered), [`offsite-backups.md`](procedures/offsite-backups.md) (etcd / Vault Raft / Calico to S3), [`pbs-restore-test.md`](procedures/pbs-restore-test.md), [`burst-substrate.md`](procedures/burst-substrate.md) (ephemeral DigitalOcean cluster and the restore drill).
- **Upgrades and patching:** [`k3s-upgrade.md`](procedures/k3s-upgrade.md), [`proxmox-host-patching.md`](procedures/proxmox-host-patching.md).
- **Secrets and access:** [`credential-rotation.md`](procedures/credential-rotation.md), [`secret-mirroring.md`](procedures/secret-mirroring.md) (Vault ↔ 1Password), [`vault-tls.md`](procedures/vault-tls.md), [`operator-ssh-agent.md`](procedures/operator-ssh-agent.md).
- **Storage and validation:** [`storage-tiering.md`](procedures/storage-tiering.md) (tiering design, still the reference for the Synology layout), [`infra-health-check-validation.md`](procedures/infra-health-check-validation.md) (the `infra-health-check` synthetic-failure matrix).
- **CI:** [`ci.md`](procedures/ci.md) (the gate and the `main` ruleset); K8s changes are tested on a burst cluster per [`k8s-burst-test.md`](procedures/k8s-burst-test.md).
- **Offsite and test substrate (Phase 10):** [`offsite-do1.md`](procedures/offsite-do1.md), [`offsite-watcher.md`](procedures/offsite-watcher.md), [`canary-pool.md`](procedures/canary-pool.md).
- **AIOps (Phase 10):** diagnosis [`aiops-diagnosis.md`](procedures/aiops-diagnosis.md), actions [`aiops-actions.md`](procedures/aiops-actions.md), autonomy [`aiops-autonomy.md`](procedures/aiops-autonomy.md), rebuild [`aiops-rebuild.md`](procedures/aiops-rebuild.md), forecasting [`aiops-forecasting.md`](procedures/aiops-forecasting.md), drift [`aiops-drift.md`](procedures/aiops-drift.md), PR author [`aiops-author.md`](procedures/aiops-author.md), incident drafts [`aiops-incident-drafts.md`](procedures/aiops-incident-drafts.md), rightsizing [`aiops-rightsizing.md`](procedures/aiops-rightsizing.md). Code and schemas: [`aiops/README.md`](../aiops/README.md).
- Executed one-off runbooks (AGH cutover, NetBox import, Zabbix SAML deploy, the 2026-05-17 rename rebuild): [`procedures/archive/`](procedures/archive/).

## Outline wiki

[`outline/`](outline/README.md) holds the reader-shaped pages synced to the live Outline wiki (`sync_outline.py`); the repo copy is the source of truth.

## Navigating

- **Working on a service:** its `services/` doc, the matching `known-issues/` file, and any `incidents/` that touched it.
- **Questioning a choice:** [`decisions.md`](operations/decisions.md).
- **Planning new work:** the pre-flight in [`../CLAUDE.md`](../CLAUDE.md): search `docs/`, scan `open-questions.md` for prerequisites, read the known-issues files, then sequence prerequisites first.
- **Building from scratch:** [`teardown-rebuild.md`](procedures/teardown-rebuild.md).
