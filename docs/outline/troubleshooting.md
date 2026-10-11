<!-- docs/outline/troubleshooting.md -->

# Troubleshooting

Symptom-first triage. Start from what you see, not from which component you suspect. Each area page lists symptoms, the likely cause and the first check; the repository's `docs/known-issues/` has the full rule, reason and recovery for each.

---

## The first three things to check

1. **Is it the network or DNS?** From a client, resolve a name and connect by IP. Internal names failing while IPs work means DNS (AdGuard VIP `10.0.10.200`).
2. **Is Postgres reachable on the VIP?** Several apps stop at once when `10.0.10.210` is down or has no leader.
3. **What changed?** The drift-check output, the last merged PR, and a node that rebooted. Most outages follow a change.

Two reminders: a MetalLB VIP does not answer ping (test with TCP), and a Helm release that "succeeded" does not prove its values were applied.

---

## Index by symptom

| You see | Go to |
|---|---|
| A pod stuck in `ContainerCreating` with a mount error, a volume read-only, "Multi-Attach", PVC cannot be created | **Storage & iSCSI** |
| An internal name returns NXDOMAIN, a VIP does not respond, DNS is stale after failover, LoadBalancer traffic times out | **DNS & networking** |
| A node will not rejoin, a rollout is stuck, a HelmRelease will not reconcile, a Secret will not sync, a Job will not update | **Kubernetes & Flux** |
| Vault is sealed, a token or AppRole fails, Postgres rejects a client, a write hits a replica, a failover looks wrong | **Vault & Postgres** |
| A login redirects to the wrong host, an app denies a valid user, a certificate will not renew, SSH reports a host key change | **Identity & edge** |

## By alert

| Alert source | Usually means | Start with |
|---|---|---|
| Hermod `critical` | Quorum lost or a service hard-down | The area page matching the service, then **Procedures** |
| Hermod `alert`: drift detected | The fleet differs from Git | The drift-check diff in Semaphore; update the IaC or apply it |
| Weekly restore test silent on Wednesday | The schedule or Semaphore stopped | Semaphore template history |
| Zabbix QuickSync test failing | Jellyfin lost its GPU device | The group membership and the `i915` journal on Urd |
| Skuld missing | A hard freeze (known hardware fault) | **Hypervisors** (Hardware); quorums tolerate one node |

---

## Habits that prevent most mistakes

- Look at live state before editing: re-read the file, fetch upstream, grep the line.
- After a change that must survive a reboot, reboot the node.
- Never `kubectl apply` to fix production: fix Git and let Flux apply, or force a reconcile.
- Never put a secret literal in a command; if one leaks, rotate it.

---

## See also

- **Procedures**: when the fix is a runbook.
- **Components & interactions**: how the failing piece connects to its neighbours.
