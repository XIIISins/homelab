# jellyfin

Jellyfin media server in the **privileged** LXC 1123 on Urd, transcoding on the Intel iGPU (QuickSync) and never on the CPU.
Plan and rationale: [`docs/operations/5h-jellyfin.md`](../../../docs/operations/5h-jellyfin.md). Playbook:
[`playbooks/asgard-jellyfin.yml`](../../playbooks/asgard-jellyfin.yml). Host side: [`proxmox-host`](../proxmox-host/) (`gpu.yml`).

## What it does

| Step (tag) | Effect |
|---|---|
| preflight (`jellyfin:preflight`) | Asserts the container reports `lxc` (so the hardening role's branches apply) and that `/dev/dri/renderD128` is a character device owned by gid 993 |
| accounts (`jellyfin:accounts`) | Pins the `render` group to the host's gid 993 (before the package, whose systemd would pick a dynamic one) and creates `media` (gid 2000) |
| media (`jellyfin:media`) | `nfs-common`; fstab + mount `10.0.254.20:/volume5/media-backup` at `/media`, **read-only**, NFSv4.1, `hard`, by IP (not DNS); asserts it is mounted `ro` |
| install (`jellyfin:install`) | Official apt repo with the signing key verified by checksum; `jellyfin-server`, `jellyfin-web`, `jellyfin-ffmpeg7` at **exact versions**, on `dpkg hold`; a unit drop-in `RequiresMountsFor=/media /var/cache/jellyfin` so Jellyfin never starts against an empty mount; jellyfin user in `render`, `video`, `media` |
| service (`jellyfin:service`) | Enables/starts, waits for `/health` to say `Healthy` |
| gpu (`jellyfin:gpu`) | `vainfo` as the jellyfin user must show the iHD driver and the H.264 / HEVC Main10 decode profiles (fails the play, not first playback); prints the VLD profile list the J4 decoder checkboxes come from |
| monitoring (`jellyfin:monitoring`) | Hourly `jellyfin-qsv-smoke.timer` (h264_qsv + 10-bit hevc_qsv, 5 s each) writing `<ok> <epoch> <iso> <detail>` to `/var/lib/jellyfin-qsv-smoke/status`; runs once during the play and **fails it** if QSV is broken; zabbix-agent2 UserParameters (`jellyfin.qsv.smoke.ok`, `.age`, `jellyfin.media.mounted`, `jellyfin.health`) |
| config (`jellyfin:config`) | J4 settings (QSV, decoders, tone mapping, transcode path, LAN/proxies, metrics) through Jellyfin's configuration API. **Inert until an API key exists in Vault** at `secret/ansible/jellyfin/api-key`; reads, compares, POSTs only on a difference, and fails loudly on a setting name this Jellyfin version does not have |

## Operator steps this role cannot do

1. NFS permission rule for `10.0.11.223` on `volume5/media-backup` in DSM (before the first full run).
2. First-run wizard (admin account, libraries: real-time monitoring **off**, no NFO/artwork saving into the read-only mount), then an API key into Vault for `jellyfin:config`.
3. Zabbix server side: items/triggers for the UserParameters above (J6).

## Bumping Jellyfin

Change `jellyfin_version` / `jellyfin_ffmpeg_version` in `defaults/main.yml` (look the versions up in
`https://repo.jellyfin.org/debian/dists/trixie/main/binary-amd64/Packages`), review, merge, run `--tags jellyfin:install`.
The packages are held, so nothing else moves them.

## Gotchas

- `/var/cache/jellyfin` is a separate Terraform mount point with `backup = false` (transcodes and image cache); `/var/lib/jellyfin`
  (config, SQLite, metadata) is on the backed-up rootfs. SQLite never goes on NFS.
- A privileged container shares the host kernel: the hardening role's sysctls are all per-network-namespace, and its AppArmor and
  module tasks are gated on `virtualization_type != lxc`, so nothing here reaches the host.
