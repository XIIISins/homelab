<!-- ansible/roles/local-path-disk/README.md -->
# local-path-disk

Prepares a dedicated node-local data disk on K3s **workers** for
`local-path-provisioner`. The playbook includes the role **twice**, with
different parameters:

| Instance | Disk | SCSI address | Mount | Size window | Storage class | PBS |
|---|---|---|---|---|---|---|
| default | `scsi1`, 50 GB | `0:0:0:1` | `/data` | 40-60 GiB | `local-path` | backed up |
| `scratch-disk` | `scsi2`, 100 GB | `0:0:0:2` | `/scratch` | 90-110 GiB | `local-path-scratch` | **not** backed up |

The scratch disk holds cache-class bulk data (SABnzbd's `incomplete/`). It is a
separate disk so that churn never reaches PBS and a full scratch disk cannot
starve Vault Raft on `/data`.

Separates persistent local data from the OS/ephemeral root disk: if a worker's
OS is corrupted or the VM is rebuilt, the data disk can be detached and
reattached (mounted by `UUID`) to preserve stateful data. Pairs with the
`scsi1` 50 GB disk added to each worker VM in
`terraform/proxmox/asgard-k3s/main.tf`.

## What it does (idempotent)

1. **Resolves the data disk by stable SCSI address** (`*-scsi-0:0:0:1` in
   `/dev/disk/by-path/`; `:2` for the scratch instance), NOT a `/dev/sdX` name. iSCSI LUNs (Vault's PVC etc.)
   also claim `sd*` letters with unstable ordering, so a `/dev/sdb` assumption
   could target a live LUN. The scsi1 virtual disk is always at SCSI `0:0:0:1`;
   iSCSI is `ip-…-iscsi-…` and can't collide. Skips gracefully if absent (safe
   for not-yet-provisioned nodes + drift-check).
2. Installs `xfsprogs`; asserts the resolved device is ~50 GiB (defence against
   a mis-set SCSI address) before touching it.
3. `mkfs.xfs` (skips if a filesystem already exists — never reformats).
4. Mounts it at `/data` and persists in `/etc/fstab` by `UUID`, with
   `nofail` (a bad entry can't hang boot) and a blanket SELinux
   `context="system_u:object_r:container_file_t:s0"` mount option.

## Why the blanket `context=` mount (not a file label)

Workers run SELinux `Enforcing`; without `container_file_t`, the provisioner's
helper pod `mkdir` is denied (`Permission denied` even as root) and PVCs never
bind. Because these worker VMs are **exclusively K3s** — only pods/containers
ever write here — the whole filesystem is labelled container-writable at the
mount layer (`context=` option) rather than relabelling a subtree with
`semanage`/`restorecon`. Simpler, no semanage tooling, and `s0` (no MCS
categories) is reachable by every pod's `container_t:s0:cN` context. local-path
PV dirs land directly under `/data`. See CLAUDE.md "Known gotchas →
local-path-provisioner".

## Scope / safety

- **Workers only** (CPs are tainted; no local-path PVCs land there). Gated in
  `playbooks/asgard-k3s.yml` with `when: "'k3s_worker' in group_names"`.
- **Run `serial: 1` + reboot-test** each worker after first apply: Vault's
  StatefulSet keeps 2/3 quorum through a single-worker bounce, and the reboot
  validates the fstab entry actually mounts on boot (persistence rule).

## Key variables (`defaults/main.yml`)

| Var | Default | Purpose |
|-----|---------|---------|
| `local_path_disk_scsi_address` | `0:0:0:1` | SCSI address (scsi0/`0:0:0:0` = OS, scsi1 = `/data`, scsi2 = `/scratch` at `0:0:0:2`) |
| `local_path_disk_mountpoint` | `/data` | whole-volume container-writable mount |
| `local_path_disk_selinux_context` | `system_u:object_r:container_file_t:s0` | blanket FS label |
| `local_path_disk_min_gib` / `_max_gib` | `40` / `60` | mkfs safety window |

## Adding the scratch disk to a running worker

`terraform apply` hot-adds `scsi2` (`reboot_after_update = false`); the guest
normally sees it at once. If `ls /dev/disk/by-path/*-scsi-0:0:0:2` is empty,
rescan: `echo '- - -' > /sys/class/scsi_host/host0/scan`. The role skips a node
whose disk is absent, so it is safe to run before every worker has it. Then
`ansible-playbook playbooks/asgard-k3s.yml --tags scratch-disk` and reboot-test
one worker at a time (the fstab entry must mount on boot).
