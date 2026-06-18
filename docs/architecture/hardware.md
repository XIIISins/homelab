<!-- docs/architecture/hardware.md -->

# Hardware

| Device | CPU | RAM | Role | Norse name |
|--------|-----|-----|------|------------|
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | Proxmox node 1 | **Urd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4, 1TB disk | Proxmox node 2 | **Verd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | Proxmox node 3 | **Skuld** |
| Synology DS223J | Realtek RTD1619B | 1 GB | NAS | **Munin** |

**Storage tier (verified 2026-05-21 via `nvme id-ctrl`):**
- Urd: Lexar NM790 1TB NVMe (Gen 4, DRAM-less, HMB 3.0)
- Verd: Samsung 970 EVO 1TB NVMe (Gen 3, DRAM-equipped)
- Skuld: SK Hynix PC300 512GB NVMe (Gen 3, DRAM-equipped — `hmpre=0, hmmin=0` confirms direct DRAM, not HMB)
- For etcd fsync consistency: Verd ≈ Skuld > Urd. Urd's DRAM-less NVMe is the slowest of the three for sustained sync workloads even though it's Gen 4 (HMB adds PCIe round-trip latency per metadata update). All three are well within etcd's tolerance — much better than the original Urd mSATA.

**Hardware notes:**
- **Urd hardware migration, 2026-05-19 to 2026-05-21:** Migration trail was DeskMini JB95 → NUC7 (intermediate) → MSI Cubi, not a direct swap. The NUC7 was a brief stop that passed a 24h burn-in but proved hardware-flaky once running production Proxmox workload — crashed three times during einherjar-urd's life on it. Memory + disk were physically swapped from the NUC7 into the Cubi when replacement hardware arrived. Final hardware is the MSI Cubi (i3-1215u + 32GB DDR4 reused + 1TB Lexar NM790 NVMe). The original 2026-05-14 etcd-storm root cause (slow N5095 + slow mSATA fsync) **no longer exists**. The "never run CP on Urd" rule is retired. Phase 4b (Göndul Verd → Urd migration) applied 2026-05-22 as the deliberate realisation of this. One of the NUC7 crashes corrupted einherjar-urd's SSH hostkey files mid-write; see 2026-05-21 evening incident log entry. A separate NUC7-era partial migration left orphan LVs that blocked Phase 4b's first clone attempt on Urd — see 2026-05-22 incident log entry for the recovery.
- **Verd hardware migration, 2026-05-23 (Phase 4c):** Beelink MINI-S12 (N100, 16GB) → MSI Cubi (i3-1215u, 32GB). Same-node refresh: SSD transplanted, hostname/identity/IQN preserved. Procedure: live-migrate VMs/LXCs off Verd → shutdown → SSD transplant → boot new Cubi → fix NIC name → OS updates + reboot test → migrate workloads back. No workload-affecting downtime; cluster stayed 3/3 throughout. Only surface was NIC rename `nic0` (Beelink UEFI-labeled) → `enp45s0` (Cubi predictable-naming), fixed at console with vim search-and-replace in `/etc/network/interfaces` + `ifreload -a`. Both Cubi nodes now identical hardware (i3-1215u / 32 GB DDR4). The NIC-rename class is documented as a Known gotcha (`docs/known-issues/networking-multi-homed-workers.md`) — silently hit during the Urd refresh too but not surfaced then.
- **Skuld hardware refresh, confirmed live 2026-05-31:** Beelink MINI-S12 (N100, 16GB) → MSI Cubi (`PRO ADL-U Cubi 5`, i3-1215u, 32GB; verified `31 GiB` live). Kept its 512GB SK Hynix PC300 NVMe. **All three Proxmox nodes are now identical MSI Cubi i3-1215u / 32 GB DDR4** — no more single-node resource pressure, and Skuld is no longer the tight node. (Refresh narrative/date predates this entry; captured here during the 2026-06-18 CLAUDE.md sweep.)
- Urd long-term: dedicated Jellyfin LXC with Intel QuickSync passthrough. The i3-1215u's UHD Graphics is substantially stronger QSV than the N5095's UHD. Currently also running Einherjar-urd (K3s worker) and the Factorio LXC (1120).
- Göndul moved Urd → Verd on 2026-05-17 (was deferred since the 2026-05-14 incident; finally fixed during the asgard rebuild). Phase 4b (2026-05-22) returned Göndul to Urd post-hardware-refresh — back to the original 2026-05-14 design intent.
- All three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 evening during the Authentik deploy. Symmetric 2vCPU/4GB across all three is the standard.
- **CP-only workload posture (Phase 4a, applied 2026-05-21):** All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Workload pods cannot land on them. Synology CSI node-plugin evicted from CPs as expected. K3s-shipped components (CoreDNS, metrics-server, calico-apiserver) and DaemonSets that tolerate the taint (Calico-node, kube-proxy, metallb-speaker) remain. 4 GiB is the correct CP size with this posture. **Caveat surfaced during deploy:** evicting CSI from CPs is *not* a free win — stateful pods left on a CP at taint-time hang in Terminating because kubelet can't reach the missing CSI driver. See [incident log](../incidents/README.md) for the full picture.
- All nodes 1 GbE. No 2.5 GbE planned.

## Naming convention
- **Proxmox cluster:** `niflheim`
- **Hypervisor nodes:** Urd (eldest/primary, 32GB), Verd, Skuld — the three Norns
- **NAS:** Munin — Odin's raven of memory
- **DNS zones:** `midgard.xiiisins.com` (public) + `niflheim.xiiisins.com` (internal)
- **Asgard K3s CP:** Göndul, Hlökk, Sigrún (Valkyries)
- **Asgard K3s workers:** Einherjar-urd/verd/skuld (army of the Norns)
- **Jotunheim K3s CP:** Rota, Hildr, Kára (Valkyries)
- **Jotunheim K3s workers:** Drengr-urd/verd/skuld (heroes of the Norns)
- **AdGuard Home:** Saga (primary), Mimir (replica), Kvasir (replica)
- **Asgard PostgreSQL:** Fulla (primary), Vör (replica), Idunn (replica) — Frigg's handmaidens / keeper goddesses
- **Asgard PG HAProxy/etcd trio:** Hlin, Eir, Snotra — Frigg's handmaidens fronting the PG cluster. Hlin (protection — HAProxy traffic gate), Eir (healing/recovery — failover restoration), Snotra (wisdom/decision — etcd consensus).

**Cluster naming meta-principle.** The primary node defines the theme; replicas are a logical expansion within it. Postgres is the *library* — Fulla guards the treasure chest, Vör knows what is in it, Idunn preserves what must not be lost. The HAProxy/etcd trio continues the same theme by function: Hlin shields the cluster's edge, Eir mends after a node falls, Snotra arbitrates which node leads. New service clusters follow the same shape: pick the primary by archetype, the replicas (and the surrounding service trio if any) by the natural cohort that surrounds that archetype.

ASCII-only for hostnames (the inventory keys are `fulla`, `vor`, `idunn`, `hlin`, `eir`, `snotra`); display names in tables keep the accents.

## Network hardware

| Device | Purpose |
|--------|---------|
| UniFi Cloud Gateway Ultra (UCG-Ultra) | Router, firewall, VLAN routing, zone-based firewall. Static WAN IP. **Sole firewall policy boundary** (KPN is in DMZ → UCG mode). |
| KPN Experia Box | ISP-provided modem/router. Configured in "exposed host" / DMZ mode pointing all unsolicited inbound (IPv4 + IPv6) at UCG-Ultra WAN. Outbound NAT for `192.168.2.0/24` family devices only. Not in IaC — change record lives in these docs. |
| Tailscale on Synology (Docker) | OOB access — survives complete homelab failure |
| Existing dumb switches (×2) | Pass VLAN tags transparently |

## Physical topology

```
Internet
  └── KPN Experia Box (192.168.2.0/24) — DMZ → UCG-Ultra WAN
        ├── Settop box, family devices — unchanged
        └── UCG-Ultra WAN (static IP, public)
              ├── LAN 1 → Dumb switch (your room)
              │             ├── MacBook dock (VLAN 60, static 10.0.60.10)
              │             ├── Game PC (VLAN 60)
              │             └── Hue bridge (VLAN 60)
              └── LAN 2 → Dumb switch (spare bedroom)
                            ├── Urd   (10.0.254.11)
                            ├── Verd  (10.0.254.12)
                            ├── Skuld (10.0.254.13)
                            └── Munin (10.0.254.20)
```

## Proxmox cluster

- **Version:** PVE 9.x (Debian 13 Trixie)
- **Cluster name:** `niflheim`
- **No-subscription repo** on all nodes
- **VLAN-aware bridge** (`vmbr0`) on all nodes
- **Proxmox root LV:** 20GB — leaves ~94GB LVM-thin on Urd/Skuld, ~950GB on Verd
- **VM/LXC disks:** local LVM-thin
