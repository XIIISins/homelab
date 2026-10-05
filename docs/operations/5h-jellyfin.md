<!-- docs/operations/5h-jellyfin.md -->

# Phase 5h — Jellyfin (QuickSync LXC on Urd): plan

*Drafted 2026-10-05. Status: 🔲 **plan only, nothing built**. Build-sequence row: [`build-sequence.md`](build-sequence.md) "5h — Remaining LXCs". Decision row: [`decisions.md`](decisions.md) "Jellyfin" (privileged LXC on Urd, QuickSync `/dev/dri` passthrough). Service page (planned shape): [`../outline/services-and-purpose/jellyfin.md`](../outline/services-and-purpose/jellyfin.md). Steps are labelled **J0–J6** so they don't collide with the existing `5h.2` (Hermod) and `5h.3` (Semaphore) rows.*

---

## Goal and boundary

A household media server that transcodes on the Intel iGPU, never on the CPU, and keeps doing so across host patching, kernel updates, container rebuilds and migrations. "GPU passthrough doesn't give issues" is turned into three concrete properties:

1. **Built from IaC only.** The device, its permissions and the group mapping are declared in Terraform and Ansible, never hand-edited into `/etc/pve/lxc/1123.conf` (raw `lxc.cgroup2.devices.allow` + `lxc.mount.entry` lines are what usually breaks on a rebuild or a PVE upgrade).
2. **Verified by the hardware's own report.** Jellyfin's codec checkboxes are ticked from what `vainfo` says the iGPU decodes, not from a guide, and GuC/HuC firmware state is checked on the host before low-power encoding is turned on.
3. **Continuously proven.** A timer inside the LXC runs a short QuickSync transcode and Zabbix alerts when it fails. A broken passthrough (host kernel update, a renumbered render node, a GPU hang) shows up as an alert within the hour, not as a family member saying "it buffers".

Out of scope: the *arr stack, Jellyfin in K3s, Authentik SSO for Jellyfin (the design keeps local accounts), SR-IOV / VM passthrough of the iGPU.

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
| Urd headroom ([`10d-diagnosis-chatops.md`](10d-diagnosis-chatops.md) D-f) | ~7.7 GB free on 2026-10-01, before Gná (1 GB) and Ratatoskr (512 MB) landed. Urd is the most loaded node: Factorio 8 GB, Einherjar-urd 16 GB, Göndul, Hugin, Vör, Hlin, Saga, PBS, the canaries | Jellyfin gets **3 GB** (it runs ~1 GB steady, ~2 GB during a big scan). J0 re-measures live; under 4 GB free → decision D-3 |
| Munin media share | The storage redesign deferred the media volume ("own volume, created last", [`../procedures/synology-storage-redesign.md`](../procedures/synology-storage-redesign.md)); the old `media` share table in [`../services/synology.md`](../services/synology.md) is flagged stale | **J0 prerequisite:** create the media share on its *final* volume before Jellyfin mounts it. Moving a share between volumes later changes its export path and strands NFS clients ([`storage-iscsi-synology.md`](../known-issues/storage-iscsi-synology.md)) |
| LXC features ([`lxc-proxmox.md`](../known-issues/lxc-proxmox.md)) | `device_passthrough` and `mount=nfs` both need `root@pam` ticket auth; an API token can only change `nesting` | The container lives in `terraform/proxmox/asgard-lxcs-root/` (the Tailscale trio's module), not the main one |
| NFS inside an LXC (PBS precedent, LXC 1101) | PBS mounts its Munin share inside the container (`features: mount=nfs` + LXC fstab), so it doesn't depend on the host | Same pattern for `/media`, mounted **read-only** |
| LXC reboot drift ([`lxc-proxmox.md`](../known-issues/lxc-proxmox.md) "LXC reboot persistence") | PVE rewrites resolv.conf at start; LXC sysctls reset at interface bring-up | `initialization.dns` in Terraform (fleet standard); the hardening role's LXC branches must also apply to a privileged container (check `ansible_virtualization_type == 'lxc'` is true for it, J3) |
| PBS capacity ([`open-questions.md`](open-questions.md)) | Datastore 81 % on 2026-10-05 and rising | Transcodes and the image cache go on a separate mount point with `backup = false`; only config, the database and metadata are backed up (a few GB, dedup-friendly) |
| Front door for an LXC UI | `k8s/asgard/apps/zabbix-ingress/` (Service + hand-written EndpointSlice + HTTPRoute on the midgard Gateway) plus a `*-direct.niflheim` AGH backdoor that skips K3s | Same pattern: `jellyfin.midgard` via Traefik, `jellyfin-direct.niflheim` straight to the LXC so playback survives a K3s outage |
| Package source | No package mirror yet (5k Hvergelmir is not built); third-party repos have died on us before (SFTPGo) | Official Jellyfin apt repo, **versions pinned** in role defaults (`jellyfin`, `jellyfin-ffmpeg7`); a version bump is a reviewed PR |
| Hermod | A `media` tag (Discord channel Ölrún) already exists | Optional: Jellyfin's webhook plugin posts "new media added" there (J6) |

No Phase 0 closure from `open-questions.md` blocks this. The real prerequisites are the media share and the Urd memory check, both in J0.

## Why privileged (re-checked, still right)

PVE 8.2+ can pass a device into an *unprivileged* container too, so GPU access alone no longer forces privileged. Privileged still wins here because of the media mount: an unprivileged container can't mount NFS itself, so it would need a host bind mount (ties the container to one host's fstab and breaks `pct migrate`) and files would appear as uid 100000+ unless the NAS squashes. Privileged keeps the PBS pattern: the container mounts its own share, uids are 1:1, and any of the three identical nodes can run it. Mitigations for the larger blast radius: media is mounted read-only, Jellyfin runs as the unprivileged `jellyfin` user, only the render node is passed (not `card0`, not all of `/dev/dri`), and the container is not reachable from outside the LAN/tailnet.

## Design

```
clients (LAN, tailnet) ── https ──> Traefik VIP 10.0.20.10 (jellyfin.midgard, midgard wildcard cert)
                                      └─ EndpointSlice ──> 10.0.11.223:8096  LXC 1123 "jellyfin" (Urd, privileged)
fallback (K3s down) ── http ──────> jellyfin-direct.niflheim → 10.0.11.223:8096
LXC 1123:  /dev/dri/renderD128 (passed, gid=render, 0660) → jellyfin-ffmpeg (iHD/QSV)
           /var/lib/jellyfin     rootfs, local-lvm, backed up    (config, SQLite DB, metadata)
           /var/cache/jellyfin   mount point, local-lvm, backup=false (transcodes, image cache)
           /media                NFS ro from Munin, mounted in-container, RequiresMountsFor
```

Identity: **LXC 1123, `10.0.11.223`, VLAN 11, Urd** (next free in the 1120–1129 services range). Sizing: 4 cores (scans, subtitle extraction and the occasional software fallback; iGPU work does not count against them), 3 GB RAM + 1 GB swap, 16 GB rootfs, 40 GB cache mount point. Not in a PVE HA group (passthrough + in-guest NFS: moves are a deliberate `pct migrate --restart`).

SQLite stays on local-lvm, never NFS (SQLite locking over NFS corrupts databases).

## J0 — Prerequisites and host checks (read-only, plus one DSM change)

- [ ] **Media share on Munin**: create it on its final volume (operator, DSM UI; Synology is not in IaC), NFS export to `10.0.11.223` only, read-only, `root_squash`, NFSv4.1. Record the export path and volume in [`../services/synology.md`](../services/synology.md).
- [ ] **Urd headroom**: `pvesh get /nodes/urd/status` + `free -g` on Urd. ≥ 4 GB free → proceed with 3 GB. Less → D-3.
- [ ] **Dual-channel RAM**: `dmidecode -t memory | grep -E 'Size|Locator'` on Urd. Two populated DIMMs = dual-channel. One DIMM → note it; tone-mapping capacity will be roughly halved (J5 measures it either way).
- [ ] **iGPU on the host**: `ls -l /dev/dri/by-path/` (expect `pci-0000:00:02.0-render -> ../renderD128`), `lspci -nnk -s 00:02.0` (kernel driver `i915`), `getent group render` (note the gid).
- [ ] **GuC/HuC firmware**: `dmesg | grep -iE 'guc|huc'` on Urd. Expect GuC submission enabled and HuC authenticated (the default on Alder Lake-P; firmware ships in `pve-firmware`). Only if HuC is not loaded: `options i915 enable_guc=3` via the `proxmox-host` role + reboot test. Without HuC, leave the low-power encoders off (J4).
- [ ] **Nothing else holds the GPU**: no `nomodeset`, no `i915` blacklist, no GVT-g / SR-IOV DKMS module on the host (Alder Lake has no GVT-g; out-of-tree SR-IOV is exactly the kind of fragility this plan avoids).

## J1 — Host role (`proxmox-host`, Urd/Verd/Skuld alike)

- Install `intel-gpu-tools` (`intel_gpu_top` for live engine use; runs on the host, a container can't read the PMU).
- Assert, on every run, that `/dev/dri/renderD128` is the `00:02.0` device. If a second DRM device ever appears (USB display adapter, a future dGPU), the render node can renumber; the assert fails the play instead of passing the wrong device.
- Apply to all three nodes, because Jellyfin may be migrated to any of them.

## J2 — Terraform

- `terraform/proxmox/asgard-lxcs-root/lxcs.tf`: `proxmox_virtual_environment_container.jellyfin` — `unprivileged = false`, `features { nesting = true, mount = ["nfs"] }`, `device_passthrough { path = "/dev/dri/renderD128", gid = <container render gid>, mode = "0660" }`, a `mount_point` at `/var/cache/jellyfin` with `backup = false`, `initialization.dns` (AdGuard VIP + UCG fallback, fleet standard), tags `asgard, lxc, jellyfin, managed-by-terraform`. Pin the render gid to a fixed number (for example 993) and have Ansible create the container's `render` group with that gid, so host, Terraform and container agree after any rebuild.
- `terraform/netbox/vms.tf`: VM + interface + IP (standing TF→NetBox rule).
- `terraform/adguard/rewrites.tf`: `jellyfin.midgard.xiiisins.com → 10.0.20.10`, `jellyfin-direct.niflheim.xiiisins.com → 10.0.11.223`.
- `inventory/hosts.yml`: new `jellyfin` group.

## J3 — Ansible (`roles/jellyfin`, `playbooks/asgard-jellyfin.yml`)

Day-1 baseline as root, then the full play as `ansible`, per the LXC bootstrap flow. The play: `baseline` → `hardening` → `jellyfin` → `zabbix-agent` → `vlagent`.

The `jellyfin` role:

- `render` group with the pinned gid; `jellyfin` user in `render` (and `video`).
- NFS: `nfs-common`, fstab entry `munin:/<export> /media nfs4 ro,vers=4.1,hard,_netdev,noatime 0 0`, mount asserted.
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

- **Libraries**: real-time monitoring **off** (inotify does not see changes made over NFS by other clients); scheduled scan every 6 h instead. Saving artwork/NFO into media folders **off** (the mount is read-only; metadata stays local). Chapter-image extraction during scan **off**; the scheduled task runs at night.
- **Trickplay**: hardware decoding + hardware MJPEG encoding on, key frames only, low priority, scheduled at night (03:00, after PBS's window).
- **Networking**: LAN networks `10.0.0.0/16` (client VLAN 60 included) and the tailnet `100.64.0.0/10`; known proxies `10.0.21.21-23` (the workers' addresses Traefik's traffic leaves from); HTTPS off in Jellyfin (Traefik terminates); auto-discovery off (clients are on another VLAN; UDP broadcast doesn't cross it). Remote bitrate limit: set from the KPN upload rate (operator input).
- **Metrics**: `EnableMetrics` in `system.xml`; `/metrics` is scraped on the direct address by vmagent, not exposed on the midgard route.
- **Users**: local accounts per the design; "allow media deletion" off for everyone (the mount is read-only anyway).

## J5 — Acceptance (before calling it done)

- [ ] `vainfo` in the LXC lists iHD and the decode profiles above; `intel_gpu_top` on Urd shows the Video/VideoEnhance engines busy during a transcode while the LXC's CPU stays low.
- [ ] Playback matrix, each confirmed as hardware in the transcode log (`h264_qsv`/`hevc_qsv` in the ffmpeg line): 1080p H.264 direct play; 4K HEVC 10-bit HDR → 1080p SDR (VPP tone map); AV1 → H.264; PGS subtitle burn-in; a VC-1 or MPEG-2 file.
- [ ] Capacity measured, not assumed: concurrent 4K HDR→1080p tone-mapped streams until one drops below 1.0× real time; same for 1080p H.264. Record the numbers in the service page. They decide the remote bitrate limit and whether HEVC output stays on.
- [ ] **Reboot test of the LXC and of Urd** (persistence rule): after Urd comes back the NFS mount, the render device permissions, the smoke timer and playback all work with no hand step.
- [ ] `pct migrate 1123 verd --restart` and back: plays on Verd's iGPU (proves portability and the J1 assert).
- [ ] PBS backup of 1123 completes; its size is small (cache mount excluded); a restore to a scratch VMID starts Jellyfin with the library intact.
- [ ] Smoke timer failure path: remove the `jellyfin` user from `render` → the next run alerts in Zabbix → re-run the play → it clears.

## J6 — Monitoring, docs and post-flight

- Zabbix: fleet agent (via `fleet-agents.yml`), HTTP check on `http://10.0.11.223:8096/health`, the QSV smoke state file (alert at `High` after 2 failed runs), NFS mount present, and a log item on Urd's kernel log for `i915 .* GPU HANG` (the most common Alder Lake media failure; a hang makes Jellyfin silently fall back to CPU or fail).
- Kernel updates: [`../procedures/proxmox-host-patching.md`](../procedures/proxmox-host-patching.md) gains one post-reboot check for Urd (or wherever 1123 lives): the smoke timer's next run is green.
- Optional: Jellyfin webhook plugin → Hermod `media` tag.
- Docs: `services/jellyfin.md` (from the outline page, as built), `services/asgard-lxcs.md` row, `architecture/network.md` IP row, `known-issues/` file for anything this surfaces, `decisions.md` rows for D-1..D-3, build-sequence tick, `CLAUDE.md` status line.

## Decisions (defaults picked; flip any)

- **D-1. Remote access.** Default **Tailscale**: family devices join the tailnet with an ACL that only allows `10.0.20.10:443` (and `10.0.11.223:8096` for the fallback); split-DNS already resolves `midgard` there. Alternatives: a UCG port-forward of a dedicated port to Traefik (works on any TV, but a new public surface and needs its own hardening); Cloudflared (rejected above: ToS risk to the whole zone).
- **D-2. Privileged vs unprivileged.** Default **privileged** (the decision row stands, reasons above).
- **D-3. Urd too tight.** If J0 finds < 4 GB free: Jellyfin starts at 2 GB and the operator decides whether Factorio (8 GB, the largest LXC on Urd) is right-sized, or Jellyfin is placed on Verd instead (same iGPU; the decision row would change from "Urd" to "any node, Urd preferred").

## Risks

| Risk | Mitigation |
|---|---|
| Host kernel update breaks i915/HuC | Smoke timer + Zabbix; post-patch check; PVE keeps the previous kernel to boot back into |
| Render node renumbers | J1 assert on every host run; only one DRM device exists today |
| GPU hang under load | Kernel-log item; i915 resets the engine itself; capacity limits come from J5 |
| NFS outage while Jellyfin runs | `hard` mount stalls reads instead of returning errors; `RequiresMountsFor` blocks a start without the mount; scheduled scans, no real-time removal |
| Privileged container escape | Read-only media, no external exposure, unprivileged service user, minimal device set |
| Urd memory pressure | 3 GB cap, cache not in tmpfs, J0 measurement, D-3 |

## Operator steps (the ones Claude cannot do)

1. Create the media share + NFS export on Munin (J0).
2. Answer D-1 (and D-3 if J0 triggers it); give the KPN upload rate for the remote bitrate limit.
3. `terraform apply` in `asgard-lxcs-root` (needs `PROXMOX_VE_PASSWORD`), `netbox`, `adguard`, from the main checkout.
4. Jellyfin first-run wizard (admin account, libraries), then create household accounts.
5. For D-1 = Tailscale: invite family users and add the ACL grant in `terraform/tailscale/policy.hujson` (a PR Claude can draft).

## Next

After J6: the *arr stack is the natural follow-on (it writes to the same media share, which is when the share becomes read-write for *something*, never for Jellyfin).
