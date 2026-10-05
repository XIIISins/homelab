<!-- docs/services/jotunheim-k3s.md -->

# Jotunheim K3s cluster

Non-critical services and experiments. Failure here doesn't cascade and doesn't block recovery — downtime of hours-to-days is acceptable. Same physical-cluster shape as asgard; the distinction is *failure-domain risk*, not "experimental vs production." Most jotunheim services have real users — just not me-needing-them-right-now users.

**Status:** ✖ **Shelved 2026-10-05: Phase 7 is dropped for lack of resource capacity** (the six VMs below need ~60 GB RAM; each node had ~6–8 GB free). Not deployed, not planned. This page is kept as the design record. K8s experiments now use the ephemeral burst cluster ([procedure](../procedures/burst-substrate.md)); the services listed below come back, if at all, as individual asgard workloads. VLANs 30/31, VMIDs 3001–3999 and the names stay reserved. See [`decisions.md`](../operations/decisions.md) "Jotunheim K3s dropped".

**Planned VMs:**

| Name | VM ID | Node | IP | Role |
|------|-------|------|----|------|
| Rota | 3001 | Urd | `10.0.31.11` | K3s CP |
| Hildr | 3002 | Verd | `10.0.31.12` | K3s CP |
| Kára | 3003 | Skuld | `10.0.31.13` | K3s CP |
| Drengr-urd | 3101 | Urd | `10.0.31.21` | K3s Worker |
| Drengr-verd | 3102 | Verd | `10.0.31.22` | K3s Worker |
| Drengr-skuld | 3103 | Skuld | `10.0.31.23` | K3s Worker |

**Services:**

| Service | Status |
|---------|--------|
| Arr stack (Sonarr/Radarr/Prowlarr/etc.) | 🔲 — media automation |
| Komga | 🔲 — manga server |
| Homepage | 🔲 — service-grid dashboard (distinct from Startpage; Startpage is in asgard) |
| Wallpaper gallery | 🔲 — toy gallery for desktop wallpapers |
| Synology CSI (jotunheim) | 🔲 — iSCSI, second StorageClass instance |
| External Secrets Operator (jotunheim) | 🔲 — pulls from same Vault as asgard instance |

Plus ad-hoc namespaces for genuine experiments (kubevirt trial, ArgoCD comparison, etc.).
