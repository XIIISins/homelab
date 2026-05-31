<!-- docs/outline/troubleshooting.md -->

# Troubleshooting

The on-call reference. Start here when something's broken and you want the fastest path from symptom to fix. Each playbook is organised symptom-first — find the thing you're seeing, read the cause, run the diagnosis, apply the fix.

*Start from what you observe, not what you suspect. The error string in front of you is more trustworthy than your theory about it.*

---

## First three things to check

Before diving into a specific playbook, these resolve or localise most incidents:

1. **Is it DNS or the network?** The lowest layers fail in ways that look like application bugs. If something "can't connect," confirm name resolution and reachability first — see **DNS & networking**.
2. **Is the storage layer healthy?** A surprising share of "pod won't start" and "service is down" traces to iSCSI sessions, the CSI controller, or the NAS — see **Storage & iSCSI**.
3. **What changed?** Flux and Semaphore both reconcile continuously; a recent commit or a drift-apply is often the trigger. Check the most recent change before assuming hardware.

---

## The playbooks

| Playbook | Covers |
|---|---|
| **Storage & iSCSI** | Mount failures, orphan sessions, the DS223J LUN cap, read-only filesystems, CSI controller down |
| **DNS & networking** | Internal NXDOMAIN, MetalLB VIP reachability, in-cluster tromboning, AdGuard sync, multi-homed worker drops |
| **Kubernetes & Flux** | Duplicate node names, stuck rollouts, failed HelmReleases, ExternalSecrets not syncing, immutable Jobs |
| **Vault & Postgres** | Sealed Vault, expired tokens, AppRole mismatches, TLS/sslmode rejections, writing to a replica |
| **Identity & edge** | Authentik outpost host caching, per-app group denials, stale Cloudflare tokens, SSH hostkey mismatches |

---

## When it's not a quick fix

Some symptoms point at a deliberate operation rather than a one-line fix — rebuilding a node, recovering Vault, rebuilding the cluster. Those live in **Procedures**. The playbooks here link across when that's the right escalation.

## See also

- **Procedures** — the deliberate operations these playbooks sometimes escalate to.
- **Components & interactions** — the architecture behind each failure mode.
