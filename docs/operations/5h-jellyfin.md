<!-- docs/operations/5h-jellyfin.md -->

# Phase 5h — Jellyfin (QuickSync LXC on Urd) + media automation (Sonarr, SABnzbd): plan

*Drafted 2026-10-05. Status: 🟡 **J0 host checks done 2026-10-06 (all pass except the DSM NFS rule, an operator step); implementation starting**. Build-sequence row: [`build-sequence.md`](build-sequence.md) "5h — Remaining LXCs". Decision row: [`decisions.md`](decisions.md) "Jellyfin" (privileged LXC on Urd, QuickSync `/dev/dri` passthrough). Service page (planned shape): [`../outline/services-and-purpose/jellyfin.md`](../outline/services-and-purpose/jellyfin.md). Steps are labelled **J0–J6** (Jellyfin) and **M0–M4** (media automation) so they don't collide with the existing `5h.2` (Hermod) and `5h.3` (Semaphore) rows.*

---

## Goal and boundary

A household media server that transcodes on the Intel iGPU, never on the CPU, and keeps doing so across host patching, kernel updates, container rebuilds and migrations. "GPU passthrough doesn't give issues" is turned into three concrete properties:

1. **Built from IaC only.** The device, its permissions and the group mapping are declared in Terraform and Ansible, never hand-edited into `/etc/pve/lxc/1123.conf` (raw `lxc.cgroup2.devices.allow` + `lxc.mount.entry` lines are what usually breaks on a rebuild or a PVE upgrade).
2. **Verified by the hardware's own report.** Jellyfin's codec checkboxes are ticked from what `vainfo` says the iGPU decodes, not from a guide, and GuC/HuC firmware state is checked on the host before low-power encoding is turned on.
3. **Continuously proven.** A timer inside the LXC runs a short QuickSync transcode and Zabbix alerts when it fails. A broken passthrough (host kernel update, a renumbered render node, a GPU hang) shows up as an alert within the hour, not as a family member saying "it buffers".

Media automation is in scope since 2026-10-05 (operator): **Sonarr + SABnzbd only** (the library is ~99 % anime, so no Radarr; no Prowlarr while there are only one or two indexers), see "Media automation" below. Out of scope: Radarr/Prowlarr/Bazarr, Jellyfin in K3s, Authentik SSO for Jellyfin (the design keeps local accounts), SR-IOV / VM passthrough of the iGPU.

## The hardware (what the settings are tuned for)

| Item | Value | Consequence for Jellyfin |
|---|---|---|
| Box | MSI Cubi 5 12M (MS-B0A8), all three nodes identical | Jellyfin can `pct migrate --restart` to Verd or Skuld and find the same iGPU (portable by design, not pinned to Urd's hardware) |
| CPU | i3-1215U, 2 P-cores + 4 E-cores, 8 threads | Too weak for software transcoding of 4K; every transcode must be hardware or it will stutter |
| iGPU | Intel UHD Graphics (Xe-LP, Gen12, 64 EU) | **Decode:** H.264, HEVC 8/10/12-bit (incl. 4:2:2/4:4:4), VP9 8/10/12-bit, AV1 8/10-bit, MPEG-2, VC-1. **Encode:** H.264, HEVC 8/10-bit (low-power VDEnc path needs HuC). **No AV1 encode** (that starts with Arc/Meteor Lake). HDR→SDR tone mapping in hardware (Intel VPP) |
| Driver | `i915` (PVE 9 kernel). Not `xe`: Alder Lake is not an `xe` platform by default; never `force_probe` it | Userspace: the iHD VA-API driver bundled with `jellyfin-ffmpeg` |
| RAM | 32 GB DDR4 | The iGPU uses system RAM. **Dual-channel matters**: single-channel halves the iGPU's memory bandwidth, which is what tone mapping is bound by. J0 checks it |
| Network | 1 GbE, media on Munin over NFS | A 4K remux is ~80–100 Mbit/s; 1 GbE carries several. NFS is the right tier for bulk sequential media ([storage tiering](../../CLAUDE.md)) |

## Pre-flight findings (what I checked)

| Check | Finding | Consequence |
|---|---|---|
| Decisions | "Jellyfin: privileged LXC on Urd, QuickSync /dev/dri passthrough". Service page: NFS media from Munin, SQLite, local accounts, `jellyfin.midgard.xiiisins.com`, external via Cloudflared | Followed, except the Cloudflared part (next row). Privileged is still the right call, see "Why privileged" |
| Cloudflared for external access | Cloudflare's terms only allow serving video through the CDN with their paid video products (Stream/R2); tunnel traffic is CDN traffic. A suspension would hit the whole `xiiisins.com` zone: Authentik, WebFinger/OIDC, MicroBin, the apex | **Do not put Jellyfin behind the tunnel.** Decision D-1 below; default is Tailscale (already live, split-DNS already serves `midgard`) |
| Urd headroom ([`10d-diagnosis-chatops.md`](10d-diagnosis-chatops.md) D-f) | ~7.7 GB free on 2026-10-01, before Gná (1 GB) and Ratatoskr (512 MB) landed. Urd is the most loaded node: Factorio (8 GB cap until 2026-10-06, 2 GB since the memory-caps PR; it really uses ~45 MB), Einherjar-urd 16 GB, Göndul, Hugin, Vör, Hlin, Saga, PBS, the canaries | Jellyfin gets **3 GB** (it runs ~1 GB steady, ~2 GB during a big scan). J0 re-measures live; under 4 GB free → decision D-3 |
| Munin media share | The storage redesign deferred the media volume ("own volume, created last", [`../procedures/synology-storage-redesign.md`](../procedures/synology-storage-redesign.md)); the old `media` share table in [`../services/synology.md`](../services/synology.md) is flagged stale | **Share exists (operator, 2026-10-05): `10.0.254.20:/volume5/media-backup`.** J0 only adds an NFS rule for `10.0.11.223`. Don't move the share to another volume later: that changes its export path and strands NFS clients ([`storage-iscsi-synology.md`](../known-issues/storage-iscsi-synology.md)) |
| LXC features ([`lxc-proxmox.md`](../known-issues/lxc-proxmox.md)) | `device_passthrough` and `mount=nfs` both need `root@pam` ticket auth; an API token can only change `nesting` | The container lives in `terraform/proxmox/asgard-lxcs-root/` (the Tailscale trio's module), not the main one |
| NFS inside an LXC (PBS precedent, LXC 1101) | PBS mounts its Munin share inside the container (`features: mount=nfs` + LXC fstab), so it doesn't depend on the host | Same pattern for `/media`, mounted **read-only** |
| LXC reboot drift ([`lxc-proxmox.md`](../known-issues/lxc-proxmox.md) "LXC reboot persistence") | PVE rewrites resolv.conf at start; LXC sysctls reset at interface bring-up | `initialization.dns` in Terraform (fleet standard); the hardening role's LXC branches must also apply to a privileged container (check `ansible_virtualization_type == 'lxc'` is true for it, J3) |
| PBS capacity ([`open-questions.md`](open-questions.md)) | Datastore 81 % on 2026-10-05 and rising | Transcodes and the image cache go on a separate mount point with `backup = false`; only config, the database and metadata are backed up (a few GB, dedup-friendly) |
| Front door for an LXC UI | `k8s/asgard/apps/zabbix-ingress/` (Service + hand-written EndpointSlice + HTTPRoute on the midgard Gateway) plus a `*-direct.niflheim` AGH backdoor that skips K3s | Same pattern: `jellyfin.midgard` via Traefik, `jellyfin-direct.niflheim` straight to the LXC so playback survives a K3s outage |
| Package source | No package mirror yet (5k Hvergelmir is not built); third-party repos have died on us before (SFTPGo) | Official Jellyfin apt repo, **versions pinned** in role defaults (`jellyfin`, `jellyfin-ffmpeg7`); a version bump is a reviewed PR |
| Hermod | A `media` tag (Discord channel Ölrún) already exists | Optional: Jellyfin's webhook plugin posts "new media added" there (J6) |

No Phase 0 closure from `open-questions.md` blocks this. The real prerequisites are the NFS rule on the media share and the Urd memory check, both in J0.

## Why privileged (re-checked, still right)

PVE 8.2+ can pass a device into an *unprivileged* container too, so GPU access alone no longer forces privileged. Privileged still wins here because of the media mount: an unprivileged container can't mount NFS itself, so it would need a host bind mount (ties the container to one host's fstab and breaks `pct migrate`) and files would appear as uid 100000+ unless the NAS squashes. Privileged keeps the PBS pattern: the container mounts its own share, uids are 1:1, and any of the three identical nodes can run it. Mitigations for the larger blast radius: media is mounted read-only, Jellyfin runs as the unprivileged `jellyfin` user, only the render node is passed (not `card0`, not all of `/dev/dri`), and the container is not reachable from outside the LAN/tailnet.

## Design

```
clients (LAN, tailnet) ── https ──> Traefik VIP 10.0.20.10 (jellyfin.midgard, midgard wildcard cert)
                                      └─ EndpointSlice ──> 10.0.11.223:8096  LXC 1123 "jellyfin" (Urd, privileged)
fallback (K3s down) ── http ──────> jellyfin-direct.niflheim → 10.0.11.223:8096
LXC 1123:  /dev/dri/renderD128 (passed, gid=2001 `igpu`, 0660) → jellyfin-ffmpeg (iHD/QSV)
           /var/lib/jellyfin     rootfs, local-lvm, backed up    (config, SQLite DB, metadata)
           /var/cache/jellyfin   mount point, local-lvm, backup=false (transcodes, image cache)
           /media                NFS ro 10.0.254.20:/volume5/media-backup, mounted in-container, RequiresMountsFor
```

Identity: **LXC 1123, `10.0.11.223`, VLAN 11, Urd** (next free in the 1120–1129 services range). Sizing: 4 cores (scans, subtitle extraction and the occasional software fallback; iGPU work does not count against them), 3 GB RAM + 1 GB swap, 16 GB rootfs, 40 GB cache mount point. Not in a PVE HA group (passthrough + in-guest NFS: moves are a deliberate `pct migrate --restart`).

SQLite stays on local-lvm, never NFS (SQLite locking over NFS corrupts databases).

## Build status (2026-10-06)

J1 (host role, PRs #207/#212), J2 (Terraform, NetBox, AdGuard, #208/#209/#213), J3 (role and playbook, #210, fixes #213/#214/#217/#220), J4 (settings through the API, converged and verified) and J6 (Zabbix templates #212, K8s ingress #211, `site.yml` #218) are done and applied. J5 is partial (below). Operator steps still open: libraries and household accounts in the Jellyfin UI, the Tailscale ACL (D-1), the remote bitrate limit. Service page: [`../services/jellyfin.md`](../services/jellyfin.md); what the build surfaced: [`../known-issues/jellyfin.md`](../known-issues/jellyfin.md).

**M0 correction:** a DSM group with a fixed gid (2000) cannot be created, and DSM NFS decides by numeric uid and mode bits, not the share ACL, so the shared-`media`-gid scheme below does not work as written. Give Sonarr and SABnzbd their own share and rule, and re-derive the permissions from the NFS behaviour documented in the known-issues file.

## J0 — Prerequisites and host checks (read-only, plus one DSM change)

- [x] **Media share on Munin**: exists at `10.0.254.20:/volume5/media-backup` (operator, 2026-10-05). Add an NFS permission rule for `10.0.11.223` (operator, DSM UI; Synology is not in IaC): read-only, `root_squash`, NFSv4.1 enabled. Then check from Urd: `showmount -e 10.0.254.20 | grep media-backup`. ✅ Rule in place 2026-10-06 with Squash "Map all users to admin", Read only (the first attempt with "Map root to guest" denied everyone; see [`jellyfin.md`](../known-issues/jellyfin.md)).
- [x] **Urd headroom** ✅ 2026-10-06: **7.6 GB available** (0.75 GB "free" is mostly reclaimable cache), swap 2.3 of 8 GB, memory PSI 0.00, load 0.4-1.1 on 8 cores, `local-lvm` 758 GB free. Read as *available* (the figure that matters; page cache is reclaimable) the gate passes: **3 GB RAM + 1 GB swap, 4 cores, D-3 not triggered.** The LXC caps trim (Factorio 8 → 2 GB, Hugin 4 → 2 GB, HAProxy/etcd trio 2 → 1 GB) lowered Urd's configured guest RAM from 44.5 to 34 GB but frees no live RAM: caps are not reservations ([`lxc-proxmox.md`](../known-issues/lxc-proxmox.md)). At the 3 GB cap Urd keeps about 4.6 GB available. **Portability:** Verd has 5.6 GB available (a migration there leaves ~2.5 GB: free room first), Skuld 7.2 GB with almost no swap but it is the node that hard-freezes.
- [x] **Dual-channel RAM** ✅ 2026-10-06, **all three nodes**: two 16 GB DDR4-2400 DIMMs on separate controllers (`Controller0/1-ChannelA-DIMM0`), so dual-channel. J5 still measures tone-mapping capacity.
- [x] **iGPU on the host** ✅ 2026-10-06, **all three nodes identical**: `pci-0000:00:02.0-render -> ../renderD128` (the only DRM render node), Alder Lake-UP3 GT1 `[8086:46b3]` on `i915`, `renderD128` is `root:render 0660`, **render gid 993** everywhere (matches the pinned gid in J2). Kernel `7.0.14-17-pve` on Urd and Verd, `-19` on Skuld.
- [x] **GuC/HuC firmware** ✅ 2026-10-06, **all three nodes**: `adlp_guc_70.bin` 70.49.4 and `tgl_huc.bin` 7.9.3 RUNNING, "HuC: authenticated for all workloads", "GUC: submission enabled", no GPU hangs this boot. (Read `journalctl -k -b`, not `dmesg`: on a node up for weeks the ring buffer has rolled past boot.) No `enable_guc` option is needed, so the low-power encoders stay **on** in J4 and the `proxmox-host` role needs no i915 change.
- [x] **Nothing else holds the GPU** ✅ 2026-10-06 (Urd): no `nomodeset`, no i915 blacklist, no DKMS/GVT-g/SR-IOV module; the `xe` module is loaded but has zero users and `i915` is the bound driver.

## J1 — Host role (`proxmox-host`, Urd/Verd/Skuld alike)

- Install `intel-gpu-tools` (`intel_gpu_top` for live engine use; runs on the host, a container can't read the PMU).
- Assert, on every run, that `/dev/dri/renderD128` is the `00:02.0` device. If a second DRM device ever appears (USB display adapter, a future dGPU), the render node can renumber; the assert fails the play instead of passing the wrong device.
- Apply to all three nodes, because Jellyfin may be migrated to any of them.

## J2 — Terraform

- `terraform/proxmox/asgard-lxcs-root/lxcs.tf`: `proxmox_virtual_environment_container.jellyfin` — `unprivileged = false`, `features { nesting = true, mount = ["nfs"] }`, `device_passthrough { path = "/dev/dri/renderD128", gid = 2001, mode = "0660" }`, a `mount_point` at `/var/cache/jellyfin` with `backup = false`, `initialization.dns` (AdGuard VIP + UCG fallback, fleet standard), tags `asgard, lxc, jellyfin, managed-by-terraform`. The device's gid is a number reserved for it (**2001**), and Ansible creates a group `igpu` with that gid. (First provisioning, 2026-10-06: the plan's "pin to the host's render gid 993" collides with the Debian 13 template, where 993 is `kvm` and `render` is 992, so the device is tied to its own group instead of to distro numbering.)
- `terraform/netbox/vms.tf`: VM + interface + IP (standing TF→NetBox rule).
- `terraform/adguard/rewrites.tf`: `jellyfin.midgard.xiiisins.com → 10.0.20.10`, `jellyfin-direct.niflheim.xiiisins.com → 10.0.11.223`.
- `inventory/hosts.yml`: new `jellyfin` group.

## J3 — Ansible (`roles/jellyfin`, `playbooks/asgard-jellyfin.yml`)

Day-1 baseline as root, then the full play as `ansible`, per the LXC bootstrap flow. The play: `baseline` → `hardening` → `jellyfin` → `zabbix-agent` → `vlagent`.

The `jellyfin` role:

- `igpu` group (gid 2001, the passed device's gid); `jellyfin` user in `igpu` (and `video`, `media`).
- NFS: `nfs-common`, fstab entry `10.0.254.20:/volume5/media-backup /media nfs4 ro,vers=4.1,hard,_netdev,noatime 0 0` (IP, not a name, like PBS: the mount must not depend on DNS), mount asserted.
- Jellyfin apt repo + pinned `jellyfin` and `jellyfin-ffmpeg7`.
- systemd drop-in: `RequiresMountsFor=/media /var/cache/jellyfin`, so Jellyfin never starts against an empty mount point and scans the library away.
- Paths in `/etc/default/jellyfin` / `system.xml`: cache and transcodes under `/var/cache/jellyfin`; metadata under `/var/lib/jellyfin`.
- `encoding.xml` and `system.xml` rendered from templates for the settings in J4 that live in files (hardware acceleration type, device, codec list, tone mapping, transcode path, metrics). Settings only the UI owns are set once at first-run and recorded in the service page.
- Passthrough self-check as a task: `/usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128` must list the iHD driver, run as `jellyfin`. A permissions or driver problem fails the play here, not at first playback.
- `jellyfin-qsv-smoke.timer` (hourly): as `jellyfin`, a 5-second QSV transcode of a generated test pattern (`-init_hw_device qsv=hw:/dev/dri/renderD128`, `h264_qsv` out, plus a `hevc_qsv` 10-bit leg) to `/dev/null`; writes `0`/`1` and a timestamp to a state file Zabbix reads.

## J4 — Jellyfin settings for this iGPU

Dashboard → Playback → Transcoding:

| Setting | Value | Why |
|---|---|---|
| Hardware acceleration | **Intel QuickSync (QSV)** | Gives the low-power encoders and VPP tone mapping; VA-API would work but loses those |
| QSV device | `/dev/dri/renderD128` | The only node passed in |
| Hardware decoding | H.264, HEVC, HEVC 10-bit, VP9, VP9 10-bit, AV1, MPEG-2, VC-1, HEVC RExt 8/10-bit, HEVC RExt 12-bit | **Tick exactly what `vainfo` lists as VLD profiles** (J3 prints them). These are the Gen12 expectations; VP8 is not decoded by Gen12, leave it off |
| Prefer OS native VA-API decoders | on | VA-API decode + QSV encode, zero-copy; the more stable path on Linux |
| Hardware encoding | on | |
| Intel low-power H.264 / HEVC encoders | **on, only if J0 found HuC loaded** | Uses the fixed-function VDEnc block: faster and leaves the EUs free for tone mapping. Without HuC these fail outright |
| Allow encoding in HEVC | on | Halves bitrate for remote clients that support it; Jellyfin falls back to H.264 per client |
| Allow encoding in AV1 | **off** | No AV1 encode on this iGPU |
| VPP tone mapping | on | Intel-native HDR→SDR, the fast path |
| Tone mapping (OpenCL) | on, fallback | For streams VPP can't handle. Needs the Intel OpenCL runtime: check with `jellyfin-ffmpeg -init_hw_device opencl` in J3; install `intel-opencl-icd` only if that fails |
| Tone mapping algorithm | BT.2390, defaults otherwise | Jellyfin's recommended default |
| Transcode path | `/var/cache/jellyfin/transcodes` | Local disk, not backed up, not NFS, not tmpfs (tmpfs would count against the 3 GB) |
| Throttle transcodes / delete segments | on / on | Keeps the GPU and the cache disk idle once the client has buffered enough |
| Encoding thread count | Auto | Only matters for software fallback |
| Encoder preset | Auto | Re-check after the J5 benchmark |
| Subtitle burn-in | default (hardware overlay on QSV) | Image subtitles (PGS) are the usual trigger for a full transcode; the overlay keeps it on the GPU |

Elsewhere:

- **Libraries**: real-time monitoring **off** (inotify does not see changes made over NFS by other clients). New episodes arrive through Sonarr's Jellyfin connection, which tells Jellyfin to refresh the series on import (M3); a scheduled scan every 6 h is the backstop. Saving artwork/NFO into media folders **off** (the mount is read-only; metadata stays local). Chapter-image extraction during scan **off**; the scheduled task runs at night.
- **Trickplay**: hardware decoding + hardware MJPEG encoding on, key frames only, low priority, scheduled at night (03:00, after PBS's window).
- **Networking**: LAN networks `10.0.0.0/16` (client VLAN 60 included) and the tailnet `100.64.0.0/10`; known proxies `10.0.21.21-23` (the workers' addresses Traefik's traffic leaves from); HTTPS off in Jellyfin (Traefik terminates); auto-discovery off (clients are on another VLAN; UDP broadcast doesn't cross it). Remote bitrate limit: set from the KPN upload rate (operator input).
- **Metrics**: `EnableMetrics` in `system.xml`; `/metrics` is scraped on the direct address by vmagent, not exposed on the midgard route.
- **Users**: local accounts per the design; "allow media deletion" off for everyone (the mount is read-only anyway).

## J5 — Acceptance (before calling it done)

- [~] `vainfo` in the LXC lists iHD and the decode profiles above; `intel_gpu_top` on Urd shows the Video/VideoEnhance engines busy during a transcode while the LXC's CPU stays low. ✅ 2026-10-06: `vainfo` (iHD) lists the decode profiles and the QSV smoke test passes (H.264 and 10-bit HEVC). `intel_gpu_top` on Urd not looked at yet.
- [ ] Playback matrix, each confirmed as hardware in the transcode log (`h264_qsv`/`hevc_qsv` in the ffmpeg line): 1080p H.264 direct play; 4K HEVC 10-bit HDR → 1080p SDR (VPP tone map); AV1 → H.264; PGS subtitle burn-in; a VC-1 or MPEG-2 file.
- [~] Capacity measured, not assumed: concurrent 4K HDR→1080p tone-mapped streams until one drops below 1.0× real time; same for 1080p H.264. Record the numbers in the service page. They decide the remote bitrate limit and whether HEVC output stays on. Partial 2026-10-06: synthetic 1080p 10-bit HEVC 25 Mbps → H.264 8 Mbps saturates at ~10.8× real time in aggregate (about 10 concurrent heavy 1080p streams). 4K HDR tone-mapped not measured.
- [~] **Reboot test of the LXC and of Urd** (persistence rule): after Urd comes back the NFS mount, the render device permissions, the smoke timer and playback all work with no hand step. LXC reboot ✅ 2026-10-06 (mount `ro`, device readable, services and smoke run fine with no hand step). Urd reboot still to do.
- [ ] `pct migrate 1123 verd --restart` and back: plays on Verd's iGPU (proves portability and the J1 assert).
- [ ] PBS backup of 1123 completes; its size is small (cache mount excluded); a restore to a scratch VMID starts Jellyfin with the library intact.
- [ ] Smoke timer failure path: remove the `jellyfin` user from `igpu` → the next run alerts in Zabbix → re-run the play → it clears.

## J6 — Monitoring, docs and post-flight

- Zabbix: fleet agent (via `fleet-agents.yml`), HTTP check on `http://10.0.11.223:8096/health`, the QSV smoke state file (alert at `High` after 2 failed runs), NFS mount present, and a log item on Urd's kernel log for `i915 .* GPU HANG` (the most common Alder Lake media failure; a hang makes Jellyfin silently fall back to CPU or fail).
- Kernel updates: [`../procedures/proxmox-host-patching.md`](../procedures/proxmox-host-patching.md) gains one post-reboot check for Urd (or wherever 1123 lives): the smoke timer's next run is green.
- Optional: Jellyfin webhook plugin → Hermod `media` tag.
- Docs: `services/jellyfin.md` (from the outline page, as built), `services/asgard-lxcs.md` row, `architecture/network.md` IP row, `known-issues/` file for anything this surfaces, `decisions.md` rows for D-1..D-3, build-sequence tick, `CLAUDE.md` status line.

## Decisions (defaults picked; flip any)

- [x] **D-1. Remote access.** ✅ **Decided 2026-10-05 (operator): Tailscale.** Family devices join the tailnet with an ACL that only allows `10.0.20.10:443` (and `10.0.11.223:8096` for the fallback); split-DNS already resolves `midgard` there. Alternatives: a UCG port-forward of a dedicated port to Traefik (works on any TV, but a new public surface and needs its own hardening); Cloudflared (rejected above: ToS risk to the whole zone).
- [ ] **D-2. Privileged vs unprivileged.** Default **privileged** (the decision row stands, reasons above).
- [x] **D-3. Urd too tight.** ✅ **Not triggered 2026-10-06** (7.6 GB available, see J0). If J0 had found < 4 GB free: Jellyfin starts at 2 GB and the operator decides whether any remaining over-sized cap on Urd can shrink (Factorio already went 8 → 2 GB, which frees no live RAM: caps are not reservations, see [`lxc-proxmox.md`](../known-issues/lxc-proxmox.md)), or Jellyfin is placed on Verd instead (same iGPU; the decision row would change from "Urd" to "any node, Urd preferred").

- [x] **D-4. Where Sonarr + SABnzbd run.** ✅ **Decided 2026-10-05 (operator): in K8s.** Asgard K3s (the only cluster since jotunheim was dropped the same day). Rejected: an LXC next to Jellyfin (simplest, no K8s involved, but a second pattern to undo later and it competes with Jellyfin for Urd's memory);.
- [x] **D-5. Downloader.** Default **SABnzbd** (named in the design; Python, well supported by Sonarr). NZBGet (the maintained `nzbgetcom` fork) is lighter on CPU during unpack and is a fair swap if worker load becomes a problem. ✅ SABnzbd, built 2026-10-08.
- [x] **D-6. Sonarr profiles as code (Recyclarr).** Default on. ✅ On: Recyclarr CronJob, built 2026-10-08.

## Risks

| Risk | Mitigation |
|---|---|
| Host kernel update breaks i915/HuC | Smoke timer + Zabbix; post-patch check; PVE keeps the previous kernel to boot back into |
| Render node renumbers | J1 assert on every host run; only one DRM device exists today |
| GPU hang under load | Kernel-log item; i915 resets the engine itself; capacity limits come from J5 |
| NFS outage while Jellyfin runs | `hard` mount stalls reads instead of returning errors; `RequiresMountsFor` blocks a start without the mount; scheduled scans, no real-time removal |
| Privileged container escape | Read-only media, no external exposure, unprivileged service user, minimal device set |
| Urd memory pressure | 3 GB cap, cache not in tmpfs, J0 measurement, D-3 |
| SABnzbd unpack starves a worker | CPU/memory limits, nice, speed cap; NZBGet (D-5) if it still shows up in worker load |
| Sonarr deletes or renames library files wrongly | Sonarr's recycle bin set to `/data/.recycle` (7-day cleanup); Jellyfin's mount stays read-only; the share is in Munin's own snapshot/backup scope (check in M0) |

## Operator steps (the ones Claude cannot do)

1. Add the NFS permission rule for `10.0.11.223` on `volume5/media-backup` (J0).
2. Answer D-3 if J0 triggers it; give the KPN upload rate for the remote bitrate limit.
3. `terraform apply` in `asgard-lxcs-root` (needs `PROXMOX_VE_PASSWORD`), `netbox`, `adguard`, from the main checkout.
4. Jellyfin first-run wizard (admin account, libraries), then create household accounts.
5. Media automation: Usenet provider + indexer accounts, their credentials into Vault, and the DSM permissions in M0.
6. Tailscale (D-1): invite family users and add the ACL grant in `terraform/tailscale/policy.hujson` (a PR Claude can draft).

## Media automation: Sonarr + SABnzbd (M0–M4)

Sonarr watches for new anime episodes, sends the NZB to SABnzbd, and imports the finished file into the library that Jellyfin reads. SABnzbd is the downloader the design already names (the old `downloads` share was its "landing zone").

### Pre-flight findings

| Check | Finding | Consequence |
|---|---|---|
| Placement ([`../services/jotunheim-k3s.md`](../services/jotunheim-k3s.md)) | The design had the arr stack in **jotunheim**, but jotunheim was dropped on 2026-10-05 for lack of RAM ([`decisions.md`](decisions.md) "Jotunheim K3s dropped": its services come back as individual asgard workloads) | D-4 (decided 2026-10-05: in K8s): **asgard K3s**, as an ordinary app under `k8s/asgard/apps/`. Nothing here is cascade- or recovery-blocking, so it gets low-priority resource limits and nothing depends on it |
| Storage tiers ([storage invariant](../../CLAUDE.md)) | Sonarr and SABnzbd keep config in SQLite / ini files; downloads and media are bulk files; the iSCSI LUN cap is tight | Config on **local-path** (single-instance, mmap-safe, never NFS: SQLite over NFS corrupts); media and downloads on a **static NFS PV** of the media share. No iSCSI |
| Import path | A move between two NFS exports is a copy over the network (NAS → worker → NAS for every episode); a move *inside* one export is an instant rename | **One share, one mount**: `downloads/` and the library folders both live in `volume5/media`, mounted once at `/data` in both pods (the usual single-`/data` layout). Sonarr's import becomes a rename |
| Permissions (revised 2026-10-06) | DSM NFS decides by numeric uid and the POSIX mode bits, not the share ACL, and cannot hand out a fixed gid (see [`known-issues/jellyfin.md`](../known-issues/jellyfin.md)); the old plan (uid/gid 2000 `media` shared with Jellyfin) does not work | A **new dedicated DSM shared folder `media`** (volume5), not the `media-backup` tree. Both NFS rules (Jellyfin `10.0.11.223` read-only, workers `10.0.21.21-23` read-write) use Squash **"Map all users to admin"**, so every file is created and read as the DSM `admin` account whatever uid the pod or the LXC runs as: nothing to coordinate. The pods run as 1000 with umask `002` for their own sake only |
| NFS clients | Pod traffic to Munin leaves the workers via eth0 (VLAN 21) | The `media` share's NFS rule lists `10.0.21.21-23` **read-write**, and a second rule `10.0.11.223` **read-only** for Jellyfin (which then switches `jellyfin_media_nfs_source` from `media-backup` to `media`) |
| Worker load | Workers are 2 vCPU / 16 GB and carry Vault, Victoria*, Authentik; par2 repair and unrar are CPU-heavy bursts | SABnzbd gets a CPU limit (1 core) and memory limit, runs at `nice`, and its download speed is capped so it can't fill the 1 GbE link the workers share with iSCSI |
| Notifications ([`../services/notifications.md`](../services/notifications.md)) | Hermod already has a `media` tag (Ölrún) reserved "for future Sonarr" | Sonarr's Apprise connection → Hermod `media` |
| Paid accounts | A Usenet provider and at least one NZB indexer are needed; neither exists in the repo | Operator step; credentials go to Vault (`secret/k8s/media/...`) and 1P, never in Git |

### Design

- Namespace `media` in `k8s/asgard/apps/media/`: two Deployments (`sonarr`, `sabnzbd`), `replicas: 1`, `strategy: Recreate`, images pinned to exact tags (linuxserver.io or hotio, picked in M1; bumps go through the `chart-bump` agent).
- Volumes: `sonarr-config` and `sabnzbd-config` (local-path, 2 Gi each), `media-data` (static NFS PV + PVC of `10.0.254.20:/volume5/media`, RWX, `Retain`) at `/data` in both.
- Share layout: `/data/downloads/{incomplete,complete/anime}` and `/data/anime/<Series>/Season NN/` inside the `media` share. Jellyfin mounts the same share at `/media` read-only, so its library points at `/media/anime`.
- Front door: `sonarr.niflheim.xiiisins.com` and `sabnzbd.niflheim.xiiisins.com`, internal-only HTTPRoutes on the niflheim Gateway behind the existing Authentik ForwardAuth middleware (the vmui pattern). Sonarr's own auth is set to "External" so there's one login. SABnzbd's host whitelist includes its FQDN.
- Secrets via ESO from Vault: Usenet provider login, indexer API key(s), and the Sonarr and SABnzbd API keys (seeded so they survive a config-volume loss: Sonarr through `SONARR__AUTH__APIKEY`, SABnzbd through an init container that writes `api_key` into `sabnzbd.ini`).
- Sonarr talks to SABnzbd over the cluster Service (`sabnzbd.media.svc`); SABnzbd egress is TLS to the provider on 563 (no VPN needed for Usenet over TLS).

### Anime-specific Sonarr settings

- Series type **Anime** on every series (absolute episode numbering, which is how most fansub/BD releases are named).
- Quality profile and custom formats from the TRaSH Guides anime profile (release-group tiers, BD over WEB, dual-audio preference as you like). Managed as code by **Recyclarr** (a CronJob syncing a YAML config in the repo into Sonarr), so the profile is reviewable and survives a config loss. Default on; D-6 if you'd rather click it in the UI.
- Naming: the TRaSH anime naming scheme (includes absolute number, release group and quality) so Jellyfin's anime matching works and re-imports are recognisable.
- Connections: **Jellyfin** (refresh series on import, using a Jellyfin API key from Vault) and **Apprise → Hermod** with tag `media`.
- Root folder `/data/anime`; completed-download handling on, "remove completed" on.

### Steps

- [x] **M0 — Prerequisites.** Operator: (a) DSM shared folder `media` on volume5 with `admin` read/write, plus the two NFS rules above (Squash "Map all users to admin" on both); (b) Usenet provider login seeded by hand at Vault `secret/k8s/media/usenet` (`host`, `port`, `username`, `password`, `connections`), mirrored to 1P; indexer accounts later (their API keys go to `secret/k8s/media/indexers/<name>`). Claude: Terraform for the Sonarr/SABnzbd API keys, the Authentik gate (`media-admins`), the AGH rewrites and the mirror-map entries (PR #222), then the operator applies `terraform/{vault,authentik,adguard}` and attaches the two proxy providers to the embedded outpost in the Authentik UI. ✅ 2026-10-08: share `media` (the renamed `media-backup`), NFS rules, `secret/k8s/media/{usenet,indexers/amenzb}` seeded, Terraform applied, outpost attached.
- [x] **M1 — Manifests.** Namespace, PV/PVC, the two Deployments, Services, ExternalSecrets, HTTPRoutes, AGH rewrites in `terraform/adguard/`. The k8s PR gets the burst-cluster test ([`../procedures/k8s-burst-test.md`](../procedures/k8s-burst-test.md)) before merge; merging is the deploy. ✅ #228 (burst-cluster test not run: it cannot mount Munin's NFS or run Authentik; the offline CI gate passed).
- [x] **M2 — SABnzbd.** Provider servers (TLS, connection count per provider's limit), categories (`anime` → `complete/anime`), speed cap, direct unpack on, cleanup of par2/sfv after success. ✅ Both Usenet servers test OK; memory raised to 2 Gi after an OOM (#232).
- [x] **M3 — Sonarr.** Download client = SABnzbd (category `anime`), indexers, Recyclarr sync, naming, root folder, Jellyfin + Hermod connections. ✅ Download client, ameNZB indexer, Recyclarr (#229-#231), root folder, naming, Jellyfin connection (path map `/data` to `/media`); Hermod replaced by Sonarr's native Discord connection to the same media webhook (see [`notifications.md`](../services/notifications.md)).
- [~] **M4 — Acceptance.** Add one airing and one finished series: a release is grabbed, downloaded, unpacked, imported **by rename** (no copy in SABnzbd/Sonarr logs), appears in Jellyfin within a minute without a scan, and plays with hardware transcoding. Hermod posts to Ölrún. Delete the Sonarr pod and its config PVC on a scratch run: the app comes back with the seeded API keys and Recyclarr restores the profiles. Zabbix/VictoriaLogs: pod logs ship; an HTTP check on both UIs. Partial 2026-10-08: *Trapped in a Dating Sim: The World of Otome Games Is Tough for Mobs* (S1 + S2), 23 of 24 episodes imported by rename, Jellyfin shows them after a refresh and a real transcode ran on `h264_qsv`; the Discord post, the config-loss recovery test and real 4K/AV1/PGS playback are still open. Findings: [`known-issues/media-automation.md`](../known-issues/media-automation.md).

## Next

J0 (NFS rule, Urd memory check) and M0 (accounts, share layout) can run in parallel; then J1–J5 for Jellyfin, then M1–M4, because Sonarr's import test needs a working Jellyfin.
