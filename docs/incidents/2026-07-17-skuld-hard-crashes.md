<!-- docs/incidents/2026-07-17-skuld-hard-crashes.md -->

# 2026-07-17 — Skuld repeated hard crashes (OPEN — hardware fault suspected)

> **STATUS: OPEN.** Root cause not confirmed. Node is running and de-risked, but the fault is unresolved and expected to recur. A new session picking this up should start at [What's still open](#whats-still-open).

## Summary

Skuld (Proxmox node 3, MSI Cubi i3-1215u / 32 GB / 512 GB SK Hynix PC300) hard-crashed **three times** between 2026-07-17 and 2026-07-20. All three are **instant, unclean losses** — the journal stops mid-normal-operation with **no kernel panic, no OOM, no soft/hard-lockup, no call trace**. The kernel had no time to log anything.

The first two crashes each left an ACPI **BERT (Boot Error Record Table) hardware-error record**, which firmware only populates after a fatal machine-check. Urd and Verd — identical MSI Cubi hardware on the identical `7.0.6-2-pve` kernel — have **zero BERT records ever**, making this **Skuld-specific hardware**, not a fleet-wide kernel or software issue. Disk and thermals were ruled out.

Mitigations applied: `rasdaemon` installed to decode the failing component on the next event, and the PostgreSQL primary confirmed off Skuld. **Leading suspect remains the non-ECC RAM; a memtest86+ pass is the outstanding definitive test.**

## Timeline

All times are Skuld local (CEST). Clock verified correct and NTP-synced (chrony) throughout — an early read suggesting an RTC fault was an investigator error, see [Investigation notes](#investigation-notes).

| # | Crash | Recovery | Notes |
|---|---|---|---|
| 1 | 2026-07-17 ~16:22:35 | auto-booted 16:25:39 | BERT record on next boot |
| 2 | 2026-07-17 19:29:22 | auto-booted 19:34:30 | BERT record on next boot |
| — | *(stable ~3 days)* | — | boot `be70e757` ran 19:34 Jul 17 → 11:52 Jul 20 |
| 3 | 2026-07-20 11:52:23 | booted 16:49:32 (**~5h down**) | **no BERT record**; rasdaemon running, captured nothing |

Prior to this the node was stable: 15 days uptime (Jul 2 → Jul 17), and 14 days before that.

## Evidence

**The crash signature — instant death, no software trace.** Tail of every crashed boot is routine noise (`vlagent` file-collector warnings, `timedated` activation, `pveproxy` worker cycling) and then nothing. No panic, OOM, lockup, RCU stall, or trace in any of the three.

**BERT hardware-error records (crashes #1 and #2).** On the boot following each:

```
ACPI: BERT 0x0000000043F4A000 000030 (v01 INTEL EDK2 ...)
BERT: [Hardware Error]: Skipped 1 error records
BERT: Total records found: 1
```

Raw record decode (`/sys/firmware/acpi/tables/data/BERT`): CPER section GUID `81212a96-09ed-4996-9471-8d729c8e69ed` = **Firmware Error Record Reference**, severity field `0x01` = **Fatal**, plus a large OEM processor/platform register dump. This is the signature of a fatal, firmware-first machine-check. BERT was **absent** on the Jul 2 clean boot, so the records are genuinely new to this fault window — not stale.

**Ruled out:**
- **Not thermal** — package 54 °C at inspection, crit 100 °C, `Warning/Critical Comp. Temperature Time: 0`.
- **Not the NVMe** — SK Hynix PC300 SMART clean: `Critical Warning 0x00`, `Percentage Used 7%`, `Available Spare 100%`, 61 °C. (`Unsafe Shutdowns: 268` is lifetime-cumulative and includes these crashes — an effect, not a cause.)
- **Not kernel/fleet-wide** — Urd + Verd, same Cubi hardware, same `7.0.6-2-pve` kernel, `pve-manager/9.2.3`: zero BERT records, 14 days uptime each.
- **Not memory pressure / workload** — no OOM killer activity, and a fatal machine-check is not a workload-reachable failure mode.

**Hardware context:** BIOS **8.80, dated 2024-09-18**, board MSI `MS-B0A81`. RAM is **2 × 16 GB @ 3200 MT/s, non-ECC**, `Manufacturer: 0x0000` with blank part number — i.e. unbranded/generic SO-DIMMs. On a non-ECC platform an uncorrectable memory error produces exactly this symptom set: instant fatal reset, no OS-level log, BERT record at next boot.

**Crash #3 differs from #1/#2 and is not yet explained:**
- **No BERT record** on the following boot.
- **rasdaemon was installed and running** (since Jul 17 19:53) and captured **nothing** — `ras-mc-ctl --summary` reports `No Memory errors` / `No MCE errors`.
- **~5 hours down** (11:52 → 16:49) rather than the auto-reset seen in #1/#2, implying it **hung hard and required a manual power-cycle**.

Together these suggest crash #3 may be a *true hang* (CPU/platform wedge, nothing reportable) rather than the auto-resetting machine-check of #1/#2 — or the same fault manifesting differently. Both remain consistent with failing hardware; neither is consistent with a software cause.

*Incidental, likely unrelated:* at 03:08 Jul 20 (~8h pre-crash) `pvestatd` logged `pbs-backup: error fetching datastores - 500 Can't connect to 10.0.11.20:8007` and `Backup of VM 2003 failed`. PBS (LXC 1101) lives on Skuld. Recorded as an observation only — not established as a precursor.

## Actions taken

1. **`rasdaemon` installed + enabled on the Skuld host** (`0.8.3-1`, `systemctl enable --now`, confirmed `active`, survives reboot). Prior to this the BERT record was opaque — the kernel logged only "Skipped 1 error records" with no component attribution. rasdaemon decodes CPER/MCE events and will name the failing DIMM or CPU bank. *It was running for crash #3 and captured nothing* — see above. An empty `ras-mc-ctl --summary` on a fresh DB is expected, not an error.
2. **PostgreSQL primary confirmed off Skuld.** No manual switchover was needed — Patroni had **already failed the leader away automatically** during the crashes (cluster at timeline 13):

   ```
   Cluster: niflheim-pg (7640884869128195539)
     idunn  10.0.11.232  Leader   running     ← LXC 1132 on Verd (healthy, zero BERT)
     vor    10.0.11.231  Replica  streaming   ← LXC 1131 on Urd,  lag 0
     fulla  10.0.11.230  Replica  streaming   ← LXC 1130 on Skuld, lag 0
   ```

   A forced switchover was deliberately **not** performed — the desired end-state already held, and switching would have caused a needless blip.

## Blast radius — what lives on Skuld

| Workload | ID | Type | Behaviour on Skuld loss |
|---|---|---|---|
| `fulla` | LXC 1130 | PG replica (was primary) | Replica only now — leader safe on Verd |
| `snotra` | LXC 1135 | etcd / HAProxy trio | 1 of 3 — quorum holds (`hlin` Urd, `eir` Verd) |
| `sigrun` | VM 2003 | asgard K3s CP | 1 of 3 — cluster survives |
| `einherjar-skuld` | VM 2103 | asgard K3s worker | 1 of 3 — cluster survives |
| `pbs` | LXC 1101 | Proxmox Backup Server | **Single instance — backups offline for the outage** |
| `kvasir` | LXC 1112 | AdGuard replica | 1 of 3 — VIP `10.0.10.200` holds |
| `gjallarbru` | LXC 1115 | Tailscale | 1 of 3 — tailnet holds |

Net: the cluster design absorbs a Skuld loss well. **PBS is the only true single point of failure on the node**, and it is backup infrastructure (degrades RPO during an outage rather than taking a service down).

## Findings

1. **A journal that dead-stops with no panic/OOM/trace is a hardware or firmware-level event, not a software crash.** The absence of evidence *is* the evidence — the kernel gets no scheduling opportunity to log. Don't hunt for an application cause in this signature.
2. **Check BERT on the boot *after* an unexplained reset.** `journalctl -k -b 0 | grep -i BERT` is the cheapest hardware-fault test available and requires nothing pre-installed. Firmware populates it only after a fatal machine-check. Absent on clean boots, so its appearance is meaningful. Decode the record via `/sys/firmware/acpi/tables/data/BERT` (CPER GUID + severity field).
3. **Comparing against identical fleet hardware separates "bad node" from "bad kernel/config" in one step.** Urd and Verd run the same Cubi, kernel, and PVE build with zero BERT records — that single comparison ruled out an entire class of cause.
4. **`rasdaemon` should be baseline on all PVE hosts, not installed reactively.** It has to be running *before* the event to attribute it. Installing it after two crashes still left crash #3 undecoded — and a machine-check that hangs the box outright may never reach it at all, so it is necessary but not sufficient. → candidate for the `baseline` Ansible role.
5. **Patroni handled the leader failover unattended and correctly.** The requested "move the primary off Skuld" was already satisfied by automatic failover. **Verify current cluster state before performing a manual switchover** — an unnecessary one is a self-inflicted blip. `patronictl` is not on the default `pct exec` PATH; use `/opt/patroni/bin/patronictl -c /etc/patroni/patroni.yml list`.
6. **A crash that needs a manual power-cycle is a different failure mode than one that auto-resets** and is worth recording as such. #1/#2 auto-reset with a BERT record; #3 wedged for 5 hours with none.

## What's still open

**Root cause is unconfirmed. The node should be treated as unreliable until it is.**

1. **memtest86+ pass — the definitive outstanding test.** Non-ECC generic DIMMs are the leading suspect. Requires fully draining Skuld (live-migrate `fulla`, `snotra`, `pbs`, `kvasir`, `gjallarbru`, `sigrun`, `einherjar-skuld` to Urd/Verd). **Operator decision pending** — was offered and not yet taken.
2. **BIOS update.** Currently 8.80 (2024-09-18) on MSI `MS-B0A81`. Check for a newer release; microcode updates address some spurious machine-check classes.
3. **Decide: run degraded under watch, or drain proactively.** Currently running with the PG leader safely off-node.
4. **If it recurs:** check `ras-mc-ctl --summary` / `--errors` **first** (may now have data), then BERT on the new boot, then note whether it auto-reset or required a power-cycle.
5. **Explain crash #3's divergence** — no BERT, nothing in rasdaemon, 5h wedge. May indicate a hang path distinct from the #1/#2 machine-check.
6. **Consider adding `rasdaemon` to the `baseline` role** for all PVE hosts (finding 4).
7. **PBS single-instance exposure** on a node known to be unreliable — worth revisiting placement.

## Investigation notes

**Correction worth recording:** mid-investigation the investigator read Skuld's clock as having jumped ~3 days and briefly flagged an RTC/CMOS fault. This was wrong. The session was a long-running background session; ~3 days of real time elapsed between the first tool calls (Jul 17 19:43, minutes after crash #2) and the later ones (Jul 20 16:56). Two readings taken 3 days apart were misread as a single clock jump. **Skuld's clock was correct and NTP-synced throughout** (`timedatectl`: RTC matches system, chrony synced, `System clock synchronized: yes`). No RTC fault exists. The generalisable trap: in a long-lived session, treat "the wall clock moved" as expected, and re-read `uptime` / `--list-boots` before concluding a host's clock is broken.
