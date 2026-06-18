<!-- ansible/roles/iscsi-orphan-prune/README.md -->
# `iscsi-orphan-prune` role

Prunes stale iSCSI **node records** on K3s workers. When a pod using an
iSCSI PV migrates off a worker, Synology CSI logs the session out but
leaves the persistent node record under `/var/lib/iscsi/nodes/`. These
are harmless day-to-day, but accumulate and cause errors/delays when
`iscsid` restarts (it tries to log into every configured record). CSI
re-creates the record on the next attach, so a session-less record is
safe to delete.

Workers only — CPs are CSI-evicted (Phase 4a taint) and hold no iSCSI
sessions. Wired into `playbooks/asgard-k3s.yml` gated on
`'k3s_worker' in group_names`, mirroring `local-path-disk`.

## What it does

1. Installs `/usr/local/sbin/iscsi-orphan-prune.sh` + a systemd
   `oneshot` service + timer (`OnCalendar=*:0/30` by default).
2. Each run diffs configured node records (`iscsiadm -m node`) against
   active sessions (`iscsiadm -m session`), keyed by the per-PVC target
   IQN.

## Debounce — why a record isn't deleted on first sight

A record with no active session is only an **orphan candidate**. The
script timestamps its first session-less sighting under
`{{ iscsi_prune_state_dir }}` and deletes it only once it has stayed
session-less for `iscsi_prune_grace_seconds` (default 1h). A reappearing
session clears the timestamp. This guards against deleting a record for a
LUN that is only momentarily disconnected — an `iscsid` restart, a
transient network blip, or the CSI re-stage window — all of which are far
shorter than the grace period. No kubectl/API access is needed: "no
active session" already means "not attached here now."

## Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `iscsi_prune_state_dir` | `/var/lib/iscsi-orphan-prune` | First-seen timestamps (sha256-named files) |
| `iscsi_prune_grace_seconds` | `3600` | Session-less dwell time before deletion |
| `iscsi_prune_oncalendar` | `*:0/30` | Timer cadence |
| `iscsi_prune_dry_run` | `false` | Log would-delete, never delete |

The role-configured values reach the script via the systemd unit's
`Environment=` lines (rendered from the role vars); the script carries
built-in fallbacks for manual runs. All three are overridable at runtime
via `ISCSI_PRUNE_{STATE_DIR,GRACE_SECONDS,DRY_RUN}` env vars, which is how
the role is validated without waiting out the grace period:

```bash
# Show what the pruner currently considers orphaned, against a throwaway
# state dir, deleting nothing (run twice: first seeds timestamps, second
# reports would-delete because grace=0).
ISCSI_PRUNE_DRY_RUN=true ISCSI_PRUNE_GRACE_SECONDS=0 \
  ISCSI_PRUNE_STATE_DIR=/tmp/iscsi-prune-validate \
  bash /usr/local/sbin/iscsi-orphan-prune.sh
```

Run output (and the timer's) lands in the journal under
`iscsi-orphan-prune.service`, shipped to VictoriaLogs by vlagent.
