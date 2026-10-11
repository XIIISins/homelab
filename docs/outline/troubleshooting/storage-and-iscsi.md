<!-- docs/outline/troubleshooting/storage-and-iscsi.md -->

# Storage & iSCSI

Symptoms around volumes: pods that cannot mount, filesystems that go read-only, claims that will not bind. Most trace back to the one remaining iSCSI class or to the node-local tier.

---

## First diagnostics

```
kubectl get pv | grep -c synology        # how many iSCSI LUNs exist (the NAS ceiling is ~10)
kubectl get pods -n synology-csi -o wide # is the CSI controller up?
iscsiadm -m session ; iscsiadm -m node   # on the worker: sessions vs. stored node records
```

## Symptoms

| Symptom | Likely cause | What to do |
|---|---|---|
| `Failed to create LUN … Number of LUN reach limit` | The NAS allows about ten LUNs in total, across all volumes, and cannot be raised | Do not add iSCSI claims. Put the workload on the right tier (NFS, `local-path`, emptyDir). |
| Multi-pod `Init:0/1` hangs cluster-wide | The CSI controller (a single replica) is down, so every attach stalls | Check and restart the controller pod. |
| `non-retryable iSCSI login failure` or `max_session_count` | iSCSI LUNs are single-session; a stale session pins the LUN to the wrong node after an ungraceful restart | Log out and delete the stale node record on the affected worker; clear connected initiators in the NAS SAN manager if needed. |
| After moving an iSCSI volume to another worker, trouble later | Node records outlive sessions on the old worker | Compare `iscsiadm -m node` with `-m session`; delete records that have no session and whose volume is bound elsewhere. Recurs after every iSCSI migration. |
| Released volumes leave orphan sessions | Retain policy keeps the LUN, the last worker keeps a session | Log out and delete on that worker, then remove the LUN on the NAS. |
| A volume turns **read-only** (ext4 journal abort) after a network blip | The iSCSI session timed out | Scale the workload to zero, attach the LUN to a debug pod, run `fsck.ext4 -y` **from Alpine** (the RHEL 9 e2fsprogs is too old for the filesystem features the driver uses), detach and scale back. |
| `MountVolume.MountDevice failed … DeadlineExceeded` on first deploy | The first format of an iSCSI LUN is bound by the NAS's TRIM, roughly 10 GB a minute | Wait. Size any bootstrap job's timeouts to the volume size. |
| A non-root pod cannot write to an iSCSI volume | The driver does not honour `fsGroup` | A short init container that runs as root and chowns the mount. Keep default capabilities; dropping all breaks the chown. |
| A pod left on a tainted control plane hangs in `Terminating` | The CSI plugin was evicted from the node by the taint, so the kubelet cannot unmount | Drain stateful pods **before** applying a control-plane taint; recovery is a manual logout and a stale `VolumeAttachment` delete. |
| A `local-path` claim stays Pending with `mkdir … Permission denied` | SELinux denies the helper pod on a default-labelled path | The data disk must be mounted with the container file context. Identify the disk by its stable SCSI address, never by `/dev/sdX`: letters swap across reboots. |
| A `local-path` pod will not reschedule after a worker loss | The volume is pinned to that node | Restore the worker VM from PBS. Only replicated or refillable data is allowed on this tier. |
| An NFS-backed app breaks after a Synology folder was moved | The export path changes with the volume (`/volume1/x` → `/volumeN/x`) | `showmount -e` on the NAS, correct the client mount, and restore-verify. |
| Cannot delete a Synology volume (`Device or resource busy`) | Invisible holders: LUNs, packages, the shared-folder sync daemon, nfsd locks, indexing marks | Work the chain in order; if everything looks clear, reboot the NAS. |
| Chart bump leaves the CSI pods in `ImagePullBackOff` | The chart references a driver tag that was never published | Revert the pin; recreate the controller pod. Always verify the image exists before pinning. |

## Rules to keep

- Every PVC names its tier. There is no default StorageClass on purpose.
- Rsync migrations between tiers drop `-A` and `-X` (NFS and the SELinux-mounted disk do not store per-file labels) and verify by checksum, not `du`.
- A migrator pod for a `WaitForFirstConsumer` volume pins with `nodeSelector`, not `nodeName`, or the claim never gets its node annotation.

---

## See also

- **Storage** (Hardware) and **Storage & data** (Components): the tiers and why.
- **K3s node rebuild** (Procedures): cleaning up iSCSI state when replacing a worker.
- **Kubernetes & Flux** (this section).
