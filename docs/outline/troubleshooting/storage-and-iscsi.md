<!-- docs/outline/troubleshooting/storage-and-iscsi.md -->

# Storage & iSCSI

The homelab's persistent volumes are Synology iSCSI LUNs, one per PVC. Most storage incidents are session or attachment state that's out of sync between the cluster, the worker, and the NAS.

---

## A pod can't mount its volume

**Symptom:** a pod hangs in `Init` / `ContainerCreating`; events mention an iSCSI login failure or `Multi-Attach error`.

**Cause:** a stale iSCSI session pins the LUN — and it's usually on a *different* worker than the one the pod is trying to start on, left over from an earlier abrupt disconnect. iSCSI is single-session per LUN.

**Diagnose:** audit the whole cluster, not just the node that's failing. Compare live sessions against node records and the PV's bound node:
```
iscsiadm -m session        # live sessions
iscsiadm -m node           # persisted node records (outlive sessions)
kubectl get pv             # which node the PV expects
```
An entry in `node` but not `session`, where the PV is bound to a *different* node, is the orphan.

**Fix:** log out and delete the stale record on the source node, and clear the NAS side if needed:
```
iscsiadm -m node -T <iqn> -p 10.0.254.20 --logout
iscsiadm -m node -T <iqn> -p 10.0.254.20 -o delete
```
Then check DSM → SAN Manager → Target → Connected Initiators and disconnect any stale initiator.

---

## A new PVC stays Pending — "Number of LUN reach limit"

**Symptom:** provisioning a new PVC fails with `Failed to create LUN ... Number of LUN reach limit`, even though the NAS has free space.

**Cause:** the DS223J caps iSCSI LUNs at **~10 DSM-wide** — *not* per-Volume (adding volumes does not add slots), a hard limit on the 1 GB-RAM ARM model. One LUN per PVC means you can hit the wall well before the disk is full.

**Diagnose:** confirm you're at the cap — the count of PVs should equal the count of discoverable IQNs:
```
kubectl get pv | wc -l
iscsiadm -m discovery -t st -p 10.0.254.20:3260 | wc -l
```
If they're equal and all PVs are legitimately in use, there are no orphans to reclaim — you're at the ceiling.

**Fix:** the cap is **not raisable** on this model — the structural answer is **storage tiering**, so iSCSI carries only block-critical mmap DBs and everything else lands elsewhere: `emptyDir` for cache-class state (Redis, queues, inventory caches), `nfs-client` for file-class volumes (no LUN cost), `local-path` for replicated/mmap-safe state. With tiering in place iSCSI usage stays at a single LUN and the cap is a non-issue. (Reclaim any genuine orphans first — released LUNs from `Retain` PVCs still count; delete them in DSM → SAN Manager.)

---

## A filesystem went read-only

**Symptom:** an app starts failing writes; the volume's ext4 has remounted read-only.

**Cause:** a network blip let the iSCSI session time out, ext4 aborted its journal and remounted RO to protect itself.

**Fix:** scale the workload to zero, attach the LUN to a debug pod, and fsck it. Note that **RHEL 9's e2fsprogs can't fsck Synology-CSI-formatted LUNs** (newer ext4 features) — use an Alpine debug pod:
```
kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh
# then: apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX
```
Detach and scale the workload back up.

---

## Every volume operation is stalled cluster-wide

**Symptom:** multiple unrelated pods hang in `Init:0/1`; nothing can attach or detach.

**Cause:** the Synology CSI controller is down. It's a single-replica StatefulSet with no redundancy, so its loss stalls all VolumeAttachment operations.

**Diagnose & fix:**
```
kubectl get pods -n synology-csi -o wide
```
Recover the controller pod; volume operations resume once it's `Running`.

---

## A pod stuck Terminating on a control plane

**Symptom:** a stateful pod on a control-plane node won't terminate; kubelet logs `csi.san.synology.com not found`; the `VolumeAttachment` stays `ATTACHED=true` on the dead node.

**Cause:** the control-plane `NoSchedule` taint evicted the CSI node-plugin DaemonSet, so the pod left behind can't unmount.

**Fix:** from a debug pod on that node, `iscsiadm` logout the session, force-delete the pod (`--force --grace-period=0`), then manually delete the stale `VolumeAttachment`. Prevent recurrence by draining stateful workloads *before* tainting a control plane.

## See also

- **Storage & data** (Components) — the iSCSI/CSI architecture behind these symptoms.
- **K3s node rebuild** (Procedures) — the clean iSCSI cleanup sequence during planned node work.
- **Hardware → Storage** — the DS223J and its LUN cap.
