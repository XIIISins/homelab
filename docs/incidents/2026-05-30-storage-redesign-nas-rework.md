<!-- docs/incidents/2026-05-30-storage-redesign-nas-rework.md -->

# 2026-05-30 — Storage redesign completion + Volume1 rework + Tailscale-broke-the-LAN

## Outcome

The Synology storage redesign closed out: every K8s workload now sits on the tier its data actually needs, the DS223J iSCSI LUN cap pressure (10/10) is gone (down to **1 LUN** — garage-meta), and Volume1 was delete+recreated as the dedicated backup volume with the PBS datastore moved back onto it and verified. Net: **zero data loss**, every move checksum- or identity-verified. The day surfaced one genuine outage (Tailscale subnet-router config cut the NAS's own LAN, briefly cutting cluster storage) and a long, instructive "first/system volume won't delete" holder-hunt on the Synology.

End state of the storage tiers:
- **iSCSI (block-critical only):** garage-meta (LMDB/mmap) — the *one* remaining LUN, now on Volume2.
- **local-path (app-replicated / mmap-safe local xfs):** Vault ×3 (Raft), VictoriaLogs, VictoriaMetrics — per-worker `/data` disks.
- **NFS (file-class):** teamspeak, garage-data — Volume2 `k8s-nfs`.
- **emptyDir (cache):** authentik-redis, netbox-valkey.

## Timeline

1. **Class L — Vault iSCSI → local-path.** Raft snapshot backup off-cluster first (`operator raft snapshot save`, not a plaintext kv dump). Per-node remove-peer → wipe PVC+pod → `raft join` → KMS auto-unseal, followers first, leader (vault-0) last with `step-down`. Gated on autopilot **voter-promotion + failure-tolerance-1** between every node. Quorum never dropped. KMS-on-empty-storage validated on the vault-2 canary.
2. **Stage 2 / Class M — garage-meta iSCSI → Volume2 iSCSI.** rsync static-rebind (privileged migrator, `-aHS --numeric-ids`), checksum MATCH on all 9 meta files incl. `node_key`. Identity preserved (same node_id, layout v1, bucket `outline` + key grant). Outline reconnected seamlessly.
3. **DSM LUN cleanup** — operator deleted the 7 reclaimed LUNs; cap 10/10 → effectively 1/10.
4. **Stage 3 — Volume1 delete+recreate.** PBS datastore moved Volume1 → Volume3 (staging) → Volume1 won't delete → multi-round holder hunt (below) → NAS reboot to clear an invisible fanotify hold → Volume1 removed + recreated → PBS datastore moved back Volume3 → Volume1 → re-mounted + **restore-verified** (live backup + file restore both succeed).
5. **Tailscale on Munin** — reinstalled (its install dir was on the old Volume1), misconfigured on first bring-up → **cut the NAS's own LAN** → recovered via `tailscale down` over the tailnet IP → re-enabled correctly with `--accept-routes=false --reset`.

## Findings

### 1 — Replacing a Vault Raft node's storage is a remove → wipe → join → WAIT-for-voter dance
The chart's raft config has **no `retry_join`**, so a wiped node comes up uninitialized + sealed and must be manually `vault operator raft join`'d (KMS auto-unseals after). The load-bearing gate: a freshly-joined node is a **non-voter** until autopilot promotes it (~stabilization window), during which `raft autopilot state` shows **Failure Tolerance: 0**. Moving to the next node before promotion drops quorum. Poll `list-peers` (`Voter: true`) **and** `autopilot state` (`Failure Tolerance: 1`) between nodes; step the leader down before wiping it. Encoded in CLAUDE.md "Vault".

### 2 — `operator raft snapshot save` is the right Vault backup, not a `kv get` dump
Sealed-encrypted, captures the whole barrier (KV + policies + auth + mounts), restorable. A `kv get` dump is plaintext-on-disk + KV-only. Kept at `~/homelab-backups/vault/`.

### 3 — Class-M (iSCSI→iSCSI vol2) is the same rsync static-rebind as Class-N, identity-preserving
garage-meta is LMDB; copying the meta dir verbatim (incl. `node_key`/`cluster_layout`/`db.lmdb`) preserves the cluster's identity — it comes back as the same node with the same layout, so the S3 bucket + access-key grant stay valid and Outline reconnects with no re-keying. Verify with a content checksum diff, not `du` (FS dir-block accounting differs).

### 4 — The Synology "first/system volume won't delete" holder hunt (the big one)
DSM refused to remove Volume1 (`Device or resource busy`) through **four** successive, *different* holders — each invisible to the previous tool. Resolution order:
- **iSCSI LUN on the volume.** A LUN (even with no active session) blocks volume removal. Delete the LUN + target in SAN Manager first.
- **A DSM package install dir** (Tailscale was on Volume1). Uninstall/move it.
- **`s2s_daemon` (Shared Folder Sync).** Held `@S2S` open. `systemctl stop` wasn't enough — DSM's volume-removal flow *restarts* it (new PID), so it re-grabbed the volume. **`systemctl mask --now s2s_daemon.service`** to keep it down.
- **`nfsd` holding a stale PBS datastore `.lock`.** The kernel `VFS: opened file in mnt_point (/volume1) … comm: (nfsd)` line is the only place nfsd holders show (they're kernel-thread fds, invisible to a `/proc/*/fd` scan). The client was the rebooted-but-stale PBS LXC; cleared by rebooting PBS + the NFSv4 lease (~90s) expiring.
- **An invisible `Synotify`/fanotify mount-mark from Universal Search (`SynoFinder`).** `umount … busy` with **no `VFS: opened file` line at all** = a fanotify `FAN_MARK_MOUNT`, not a file. `synopkg stop SynoFinder` dropped it — but DSM re-invokes the `fileindex` tool during the delete, so the watch kept re-arming.

**Diagnostic toolkit (because DSM ships neither `lsof` nor `fuser`):**
- `/proc`-based holder scan: `readlink /proc/*/{cwd,root,exe}` + `/proc/*/fd/*` for userspace fds; `grep -l /volume1 /proc/*/maps` for mmaps.
- `dmesg | grep 'opened file'` for **nfsd** holders (kernel-thread, invisible to the fd scan).
- `grep volume1 /proc/mounts` for sub-mounts (note: the volume's own `subvol=/@syno` line is normal, not a sub-mount; and `grep -l volume1 /proc/*/mountinfo` matches *every* process and is a false positive).
- **When all visible holders are clear but it's still busy with no `VFS` line → it's a fanotify mount-mark or other invisible kernel hold → reboot the NAS.** A reboot drops every fanotify mark + kernel hold at once and is the community-consistent fix for the "everything's clear but still busy" tail.

### 5 — The DS223J is headless — `tailscale down` over the tailnet IP is the break-glass, not a console
No HDMI/console on the ARM NAS. When the Tailscale subnet-router config cut Munin's LAN (below), the recovery was **reach it over its Tailscale IP** (its internet path was alive — "online in tailnet, dead on LAN") and `tailscale down`. A power-cycle would have re-applied the bad saved state on boot.

### 6 — Tailscale subnet-router on Synology cut the NAS's own LAN
After reinstalling the Tailscale package and bringing it up (via the **web UI**, not the documented CLI authkey path), Munin lost its LAN IP `10.0.254.20` entirely while still showing **online in Tailscale** — which cut iSCSI+NFS to the cluster and crashlooped garage within ~1 min. Cause: Tailscale installed a routing/policy rule that diverted Munin's *own* subnet (`10.0.254.x`, inside the advertised `10.0.0.0/16` supernet) into the tailnet — the classic "advertise-the-supernet-that-contains-yourself + accept-routes / consume-exit-node" footgun (a consumed exit node also blocks LAN by default). **Fix:** `tailscale up --advertise-routes=10.0.0.0/16 --accept-routes=false --reset` — `--reset` is the actual cure (wipes whatever stray flag the web-UI/reinstall left); `--accept-routes=false` pins the safe resting state. **Root cause = web-UI auth applied default flags instead of the explicit advertiser-only CLI set** — Appendix D already warns "don't log in via the UI, use the authkey from CLI." Encoded in CLAUDE.md "Tailscale".

### 7 — A NAS reboot bounces the one remaining iSCSI target — quiesce the consumer first
garage-meta (iSCSI) is the only block LUN left. A NAS reboot drops its session, and an *actively-mounted* ext4 LUN can journal-abort → RO. Quiesce garage (scale STS → 0, which logs out the iSCSI session cleanly; Outline → 0 first) before any NAS reboot; NFS consumers (`hard` mounts) self-recover. After the reboot garage re-logs-in clean (no fsck) and its LMDB identity is intact. The clean quiesce paid off — meta came back **rw**, not RO.

### 8 — The Tailscale `configure-host` boot-task gets dropped on package reinstall
Munin's Tailscale install dir lived on the old Volume1, so reinstalling the package wiped both the saved state AND the DSM Task Scheduler boot-task (`tailscale-tun-permissions`) that re-runs `configure-host` on boot. Re-create the boot-task after any reinstall (Appendix D Step 4). The task's package-restart re-applies saved `tailscale up` state — safe only once that state is the `--accept-routes=false` set.

## Root-cause patterns

- **Invisible holders compound.** The Volume1 hunt failed five times because each fix revealed a *different* holder the previous tool couldn't see (LUN → package → masked-but-respawning daemon → kernel-thread nfsd → fanotify mark). Lesson: when the visible scans are clean but it's still busy, stop chasing individuals and reboot — the catch-all for kernel-level holds.
- **Deviating from the documented bring-up path bites.** The Tailscale outage traces directly to web-UI auth instead of the Appendix-D CLI-authkey path. The runbook's "don't use the UI" warning existed *because* of exactly this class.
- **App-level redundancy + clean quiesce = safe storage moves.** Vault Raft re-synced through node wipes; garage detached its iSCSI session cleanly before the NAS bounce. No data-layer heroics needed.

## Closed
- Storage redesign Stages 1–4 complete (tiering live, Volume1 recreated, garage-meta on vol2, all LUNs reclaimed).
- PBS datastore back on Volume1, restore-verified (live backup + file restore).
- Tailscale on Munin back up as advertiser-only subnet router, LAN intact.
- Cluster-side cleanup: dangling PV + stale iSCSI node-records cleared.

## Deferred
- Full restore-to-new-LXC test (operator, next day).
- Drop the cluster default StorageClass (explicit per-workload SC naming) — Stage 4 tail.
