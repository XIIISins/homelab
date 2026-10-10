<!-- docs/services/jellyfin.md -->

# Jellyfin (LXC 1123, built 2026-10-06)

Media server with Intel QuickSync transcoding. A privileged LXC on Urd, outside K3s, because it needs `/dev/dri`. Plan and build log: [`5h-jellyfin.md`](../plans/active/5h-jellyfin.md). The library is fed by Sonarr and SABnzbd, see [`media-automation.md`](media-automation.md). Gotchas: [`known-issues/jellyfin.md`](../known-issues/jellyfin.md).

| Item | Value |
|---|---|
| Container | LXC 1123 `jellyfin`, Urd, `10.0.11.223` (VLAN 11), privileged, 4 cores, 3 GB RAM + 1 GB swap, 16 GB rootfs, 40 GB cache mount (`/var/cache/jellyfin`, `backup=false`) |
| Terraform | `proxmox_virtual_environment_container.jellyfin` in `terraform/proxmox/asgard-lxcs-root/` (needs `PROXMOX_VE_PASSWORD`: `device_passthrough` and `mount=nfs`) |
| GPU | `/dev/dri/renderD128` passed with gid 2001 (`igpu`), mode 0660. The host side is asserted by the `proxmox-host` role (`tasks/gpu.yml`) on all three nodes |
| Software | Official Jellyfin apt repo (trixie), `jellyfin-server`/`jellyfin-web` 10.11.1+deb13 and `jellyfin-ffmpeg7` 7.1.2-1-trixie, all held; bumping is a variable change in `roles/jellyfin/defaults/main.yml` |
| Media | `10.0.254.20:/volume5/media` (the DSM shared folder `media`, formerly `media-backup`) mounted **read-only** at `/media` inside the LXC (NFSv4.1, `sec=sys`); `RequiresMountsFor=/media` on the service. The library `Anime` is `/media/Anime`, real-time monitoring off. DSM NFS rule for `10.0.11.223`: Read only, Squash "Map all users to admin" |
| Playbook | `ansible/playbooks/asgard-jellyfin.yml` (baseline, jellyfin, vlagent, zabbix-agent, hardening), in `site.yml`; inventory group `media_server` |
| Settings | `--tags jellyfin:config` converges the encoding, network and system API sections from `jellyfin_config_sections`; the API key is in Vault `secret/ansible/jellyfin/api-key`, field `key`. Skipped with a message while no key exists |

## Access

- `https://jellyfin.midgard.xiiisins.com` is an HTTPRoute on the midgard Gateway only (`k8s/asgard/apps/jellyfin-ingress/`: Service plus EndpointSlice to `10.0.11.223:8096`). It returns 502 whenever the container is down.
- Fallback when K3s is down: `jellyfin-direct.niflheim.xiiisins.com` (plain http to `:8096`).
- Remote family access is Tailscale (decision D-1); the ACL is an operator step.
- Known proxies are the three worker eth0 addresses; local networks are `10.0.0.0/16` and the tailnet range.

## QuickSync

- Hardware acceleration `qsv` on `/dev/dri/renderD128`, iHD driver from `jellyfin-ffmpeg`. HEVC and AV1 *decode*; H.264 and HEVC *encode* (low-power VDEnc, HuC is authenticated on all three nodes); AV1 encode off.
- **Smoke test:** `jellyfin-qsv-smoke.timer` runs hourly, transcoding a test pattern (H.264 and 10-bit HEVC) as the `jellyfin` user. The play fails on a failed first run. Result file read by Zabbix.
- **Capacity (2026-10-06, synthetic):** 1080p 10-bit HEVC at 25 Mbps to 8 Mbps H.264 saturates at about 10.8x real time in aggregate (1 stream 8.1x, 2 streams 10.7x, 8 streams 1.35x each), so about 10 concurrent heavy 1080p transcodes stay above 1.0x. Memory stayed near 240 MB. 4K HDR tone-mapping capacity: 2 concurrent streams (below).

## Monitoring

Zabbix template `Jellyfin` (`playbooks/files/zabbix/jellyfin.yml`): QSV smoke failing for 70 minutes (High), smoke result stale (Average), media not mounted (High), `/health` not Healthy (High). Template `Proxmox iGPU` on the three PVE hosts: a kernel-journal counter of `i915 ... GPU HANG` (Average). Both templates are imported by the host-groups bootstrap play (tag `zabbix-agent:jellyfin-template`) and must exist before the hosts link them. Logs ship through vlagent (syslog and `/var/log/jellyfin/jellyfin*.log`).

## Backup and restore

PBS backs up rootfs (config, SQLite library, metadata); the cache mount is excluded. Never put `/var/lib/jellyfin` on NFS (SQLite locking).

## Libraries and who fills them

The `Anime` library (`/media/Anime`) is filled by Sonarr; Sonarr tells Jellyfin to refresh through its Jellyfin connection (path map `/data` to `/media`, see [`media-automation.md`](media-automation.md)). The admin account, the library and the API key (Vault `secret/ansible/jellyfin/api-key`, field `key`) are operator steps. Household accounts and the Tailscale ACL (D-1) are still open.

## Verified

**2026-10-06.** Full play converges, a second run changes only the smoke run itself. After a container reboot: the NFS mount is `ro`, `renderD128` is readable by `jellyfin`, the service, smoke timer, Zabbix agent and vlagent are active, `/health` is Healthy and a fresh smoke run passes.

**2026-10-06/08, synthetic sources (no library files needed).** Each transcode through `jellyfin-ffmpeg` on the QSV path: 1080p H.264 to 1080p 7.7x real time; to 720p 10.3x; 4K HEVC 10-bit HDR to 1080p SDR with VPP tone-mapping 4.9x (without tone-mapping 5.0x); AV1 1080p to H.264 (`av1_qsv` decode) 6.8x; MPEG-2 1080p 7.2x. **4K HDR tone-mapped concurrency:** 1 stream 4.95x, 2 streams 2.8x each, 3 streams 1.9x, 4 streams 1.45x, 6 streams 0.96x, so about 5 concurrent 4K HDR tone-mapped transcodes stay above real time. The bitmap-subtitle (`overlay_qsv`) burn-in run did not report a result and is still untested.

**2026-10-08, real library.** A transcode requested through Jellyfin's own API (`main.m3u8`, H.264 720p 2.5 Mbps) on an imported episode returned its first segment in 1.2 s; the ffmpeg log shows `-hwaccel vaapi` decode and `h264_qsv` encode.

## Not covered by J5 (accepted 2026-10-10)

PGS-subtitle burn-in, VC-1, Dolby Vision profile 5 / 8.4 and HLG playback were not tested: they need real files and there is no safe download source (the Kodi samples are anonymous Google Drive/Mega shares). They use the hardware paths proven below, so test a real file if one ever misbehaves. A Urd reboot is not required (the host side is Ansible-managed).

## Proven 2026-10-10 (J5)

- **`pct migrate 1123 verd --restart` and back:** about 9 minutes each way (the 40 GB cache volume is copied whole; the rootfs is 16 GB). On Verd: `/health` Healthy, the render node arrived as `root:igpu` mode 0660, NFS still `ro`, the QSV smoke test passed on Verd's iGPU (iHD, HEVC Main10 profiles listed). Back on Urd the config is identical (same MAC, `dev0`, `mp0`, `net0`).
- **PBS backup and restore:** a backup takes 7 s and is 1.46 GB (the cache mount point is excluded). Restored into a scratch CT in 27 s: `jellyfin.db` integrity ok, 55 items and 1 user, the same as live, and the restored service starts and reports the same server id and version. Two things the test surfaced: the restored cache volume is empty and `root:root`, which made Jellyfin abort (now fixed by a boot-time tmpfiles rule), and `jellyfin.service` refuses to start without `/media` (by design, see [`known-issues/jellyfin.md`](../known-issues/jellyfin.md)).
- **Smoke failure path:** removing `jellyfin` from `igpu` made the smoke test fail and Zabbix opened "QuickSync transcode self-test failing" (High); re-running the Ansible play restored the group and the next run passed. The test also exposed a wrong trigger expression, fixed the same day (same known-issues file).
- **Playback matrix (Jellyfin's own transcode decisions).** A client profile that only plays 8-bit SDR H.264 up to 1080p and 8 Mbps forces a transcode of each file from the Jellyfin project's [official test videos](https://repo.jellyfin.org/test-videos/) (CC BY-SA 4.0, sha256-verified), in a temporary library that was deleted afterwards:

  | Source | Pipeline Jellyfin built | Speed |
  |---|---|---|
  | 4K AV1 10-bit SDR → H.264 | VAAPI decode, `h264_qsv` | 3.1x |
  | 4K HEVC HDR10 → SDR | VAAPI decode, `tonemap_opencl` (BT.2390), `h264_qsv` | 3.3x |
  | 4K AV1 HDR10 → SDR | VAAPI decode, `tonemap_opencl`, `h264_qsv` | 3.1x |
  | 4K Dolby Vision P8.1 → SDR | VAAPI decode, `tonemap_opencl`, `h264_qsv` | 2.6x |
  | 1080i MPEG-2 (generated) → H.264 | VAAPI decode, `h264_qsv` | 7.1x |

  With the same hardware path but `tonemap_vaapi`/VPP instead of OpenCL the HDR rows were within 5% (HEVC HDR10 3.5x, AV1 HDR10 3.3x, DV P8.1 2.8x).
- **4K HDR10 → 1080p tone-mapping capacity: 2 concurrent streams.** One stream runs at 2.6x real time, two at 1.3x each, three at 0.86x, on both a 40 and a 150 Mbps source (`jellyfin-ffmpeg` with Jellyfin's own pipeline, 30 s per run). `intel_gpu_top` on Urd during the runs: the **Video engine ~97% busy** (it carries both decode and encode), Render/3D ~48% (the OpenCL tone mapping), VideoEnhance 0%, GPU ~955 MHz, package power 14.4 W against 9.3 W idle. So the Video engine is the limit; plan on two simultaneous 4K HDR transcodes, and a lower remote bitrate does not raise that.
- **HDR transcodes need the Intel OpenCL runtime, which Debian 13 does not ship.** The first matrix run failed every HDR and Dolby Vision transcode ("Failed to get number of OpenCL platforms", no `/etc/OpenCL/vendors`); SDR transcodes were fine. The role now installs Intel's upstream compute-runtime packages ([`tasks/opencl.yml`](../../ansible/roles/jellyfin/tasks/opencl.yml)), pinned and held. Details and the VPP alternative: [`known-issues/jellyfin.md`](../known-issues/jellyfin.md).

