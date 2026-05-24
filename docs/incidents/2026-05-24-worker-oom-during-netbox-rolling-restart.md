<!-- docs/incidents/2026-05-24-worker-oom-during-netbox-rolling-restart.md -->

# Worker OOM cascade during NetBox rolling restart (2026-05-24)

**Phase:** Embedded in 5i.3 Stage 0.
**Duration:** ~15 minutes from first kubelet drop to all-workers-Ready.
**Outcome:** Resolved by bumping asgard K3s worker memory from 4 GB to 8 GB sequentially via `terraform apply -target=...`. New baseline.

---

## Trigger

5i.3 Stage 0 needed a NetBox pod restart to pick up the new `api_token_peppers` ExternalSecret. `kubectl rollout restart deploy -n netbox netbox netbox-worker` issued. Both new pods landed on einherjar-verd due to pod anti-affinity (urd had valkey, skuld had other workloads). Cumulative RSS demand on verd exceeded the 4 GB ceiling and triggered an OOM chain.

## Diagnosis

`einherjar-verd` showed:
- `kubectl get nodes`: `NotReady`, `kubelet stopped posting node status`
- ICMP from operator workstation: responsive (kernel IP stack alive)
- `ssh ansible@einherjar-verd`: timed out during banner exchange (userspace hung)
- Verd Proxmox host: rising IO delay reported by owner

After the operator (eventually unnecessary) Proxmox-side intervention prep, the K8s scheduler's `unreachable` toleration ran out at T+5min and the netbox pod evicted off verd. Once on einherjar-urd, urd OOMed the same way 90 seconds later. The pattern was a clear OOM-and-migrate cascade: whichever worker had the netbox pod was the one that died.

## Resolution

`terraform apply -target='proxmox_virtual_environment_vm.worker["einherjar-verd"]'` after editing `terraform/proxmox/asgard-k3s/main.tf` to bump verd's `memory = 8192`. The bpg/proxmox provider treats `memory.dedicated` as in-place updatable + reboots the VM — **no recreate**. Verd came back fresh-boot, 7.5 GiB memory, load 0.07.

NetBox pod migrated off verd onto urd. urd OOMed within 90 seconds. Same `-target` apply for urd. Then skuld. Each ran in ~2 minutes start-to-Ready.

Final state: all 3 workers at 8 GiB each. No recreate, no IP changes, no IaC drift. Skuld is the tightest host (N100/16GB total: Sigrún CP 4GB + worker 8GB + PBS + LXCs) but accepted by the owner — revisit if Skuld saturates.

After all 3 workers stable, a delegated read-only health-sweep agent confirmed cluster recovery: 5 of 5 VolumeAttachments healthy on live workers, Patroni cluster healthy (idunn primary on verd at timeline 5, fulla + vor streaming async, zero lag), 2 lingering Unknown pods on skuld self-healing through normal post-reboot CSI/iSCSI registration window. NetBox API returned HTTP 200 reliably after the third worker came back.

## Findings → rules

Already in [CLAUDE.md](../../CLAUDE.md):

1. **Worker memory floor is 8 GB**, not 4 GB. CLAUDE.md "K3s VM specs → Workers" row updated; new asgard worker baseline.
2. **`bpg/proxmox` treats `memory.dedicated` as in-place updatable + VM reboot**, no recreate. Sequential `terraform apply -target=...` is the safe way to roll a memory change across CP/worker fleet (verified during this incident).

Already documented in [decisions.md](../operations/decisions.md): "Asgard K3s worker memory baseline — 8 GB" row.

## Lesson — workload-pressure baseline drifts silently

Original 4G workers sized for the early-rebuild workload baseline (essentially Vault + a couple of small infra Helm releases). Each phase since added RSS demand (Authentik server + worker + Redis; cert-manager; Cloudflared; NetBox medium preset + Valkey + worker). The pressure climbed steadily; the 4G ceiling was perfectly fine until one rolling restart concentrated both NetBox pods on the same node.

**The lens:** when adding a new heavy workload, re-check the worker memory headroom math BEFORE the rolling restart. If cumulative VM-side requests + limits + system daemons + pod-rescheduling slop exceeds N-1 workers × current memory, the restart is a Russian roulette. Easier in retrospect — at the time, "we've been at 4G for weeks, no problem" was an anchoring bias.

**The other lens:** when a kubelet goes Unreachable mid-incident, don't assume the bare-metal VM is in trouble (kernel might be fine). The classic-but-unreliable symptom is "ICMP works but sshd doesn't" — userspace OOM-thrashing without a hard kernel panic. Diagnose by:
1. `ping` → if works, kernel's alive.
2. `ssh` with short timeout → if hangs at banner, userspace is dead.
3. `qm reset 2102` via Proxmox if you can't wait for the natural eviction timeout (5 min by default).

Verd in this incident never needed manual reset — the K8s scheduler evicted the doomed pod on schedule, the in-place TF apply triggered a clean VM reboot via the provider, and userspace came back fresh.
