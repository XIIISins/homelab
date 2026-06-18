#!/usr/bin/env bash
# Managed by Ansible — roles/iscsi-orphan-prune. Do not edit on the host.
#
# Prune stale iSCSI node records left behind after CSI pod migrations.
# A configured node record (target+portal under /var/lib/iscsi/nodes/)
# with no active session is an orphan candidate. To avoid deleting a
# record for a LUN that is only momentarily disconnected (iscsid restart,
# transient network blip, the CSI re-stage window), an orphan must persist
# across a grace period: its first sighting is timestamped under STATE_DIR
# and deletion happens only once it has been session-less >= GRACE_SECONDS.
# A reappearing session clears the timestamp. Idempotent; safe to run on a
# timer. CSI re-creates the node record on the next attach, so deleting a
# session-less record never loses data.
#
# Config comes from the systemd unit's Environment= (set from role vars);
# the fallbacks below are for manual/validation runs.
set -euo pipefail

STATE_DIR="${ISCSI_PRUNE_STATE_DIR:-/var/lib/iscsi-orphan-prune}"
GRACE_SECONDS="${ISCSI_PRUNE_GRACE_SECONDS:-3600}"
DRY_RUN="${ISCSI_PRUNE_DRY_RUN:-false}"

command -v iscsiadm >/dev/null 2>&1 || { echo "iscsiadm not found — nothing to do"; exit 0; }
mkdir -p "$STATE_DIR"
now=$(date +%s)

# Configured node records (field 2 = target IQN) and active sessions
# (field 4 = target IQN). The IQN is unique per PVC, so it is the key.
mapfile -t node_targets < <(iscsiadm -m node 2>/dev/null | awk '{print $2}' | sort -u)
session_targets=$(iscsiadm -m session 2>/dev/null | awk '{print $4}' || true)

candidates=0
deleted=0
if [ "${#node_targets[@]}" -gt 0 ]; then
  for tgt in "${node_targets[@]}"; do
    [ -n "$tgt" ] || continue
    mfile="$STATE_DIR/$(printf '%s' "$tgt" | sha256sum | cut -c1-32)"

    # Active session → not an orphan; clear any pending timestamp.
    if grep -qxF "$tgt" <<<"$session_targets"; then
      rm -f "$mfile"
      continue
    fi

    candidates=$((candidates + 1))

    # First time seen session-less → start the grace clock, do not delete.
    if [ ! -f "$mfile" ]; then
      printf '%s %s\n' "$now" "$tgt" > "$mfile"
      echo "orphan candidate (grace ${GRACE_SECONDS}s started): $tgt"
      continue
    fi

    first=$(awk '{print $1}' "$mfile" 2>/dev/null || echo "$now")
    age=$(( now - first ))
    if [ "$age" -ge "$GRACE_SECONDS" ]; then
      if [ "$DRY_RUN" = "true" ]; then
        echo "[dry-run] would delete orphan node record (orphaned ${age}s): $tgt"
      elif iscsiadm -m node -T "$tgt" -o delete 2>/dev/null; then
        echo "deleted orphan node record (orphaned ${age}s): $tgt"
        rm -f "$mfile"
        deleted=$((deleted + 1))
      else
        echo "ERROR deleting orphan node record: $tgt" >&2
      fi
    fi
  done
fi

# Garbage-collect timestamps whose target is no longer a configured record.
shopt -s nullglob
for mfile in "$STATE_DIR"/*; do
  tgt=$(awk '{print $2}' "$mfile" 2>/dev/null || true)
  keep=false
  if [ -n "$tgt" ] && [ "${#node_targets[@]}" -gt 0 ]; then
    for nt in "${node_targets[@]}"; do
      [ "$nt" = "$tgt" ] && { keep=true; break; }
    done
  fi
  $keep || rm -f "$mfile"
done

echo "iscsi-orphan-prune: ${candidates} session-less record(s), ${deleted} deleted (dry_run=${DRY_RUN}, grace=${GRACE_SECONDS}s)"
