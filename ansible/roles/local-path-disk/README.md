<!-- ansible/roles/local-path-disk/README.md -->
# local-path-disk

Prepares a dedicated node-local data disk on K3s **workers** for
`local-path-provisioner`.

Separates persistent local data from the OS/ephemeral root disk: if a worker's
OS is corrupted or the VM is rebuilt, the data disk can be detached and
reattached (mounted by `UUID`) to preserve stateful data. Pairs with the
`scsi1` 50 GB disk added to each worker VM in
`terraform/proxmox/asgard-k3s/main.tf`.

## What it does (idempotent)

1. Installs `xfsprogs`.
2. Asserts the target device (`/dev/sdb` by default) exists and is ~50 GiB —
   refuses to `mkfs` anything outside the size window (won't touch the OS disk).
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
| `local_path_disk_device` | `/dev/sdb` | scsi1 data disk (scsi0 = OS) |
| `local_path_disk_mountpoint` | `/data` | whole-volume container-writable mount |
| `local_path_disk_selinux_context` | `system_u:object_r:container_file_t:s0` | blanket FS label |
| `local_path_disk_min_gib` / `_max_gib` | `40` / `60` | mkfs safety window |
