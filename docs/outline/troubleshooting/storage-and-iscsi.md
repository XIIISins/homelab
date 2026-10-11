<!-- docs/outline/troubleshooting/storage-and-iscsi.md -->

# Storage & iSCSI

Symptoms around volumes: pods that cannot mount, filesystems that go read-only, claims that will not bind. Storage here is tiered (see **Storage & data**): `local-path` (node-local), `nfs-client` (Munin), one iSCSI class (`synology-csi-iscsi-retain-vol2`, today only Garage's metadata), and emptyDir. Match the symptom to the tier first.

```bash
SSH="ssh -o IdentitiesOnly=true -i $ANSIBLE_PRIVATE_KEY_FILE"
```

---

## 1. First diagnostics (read-only)

```bash
kubectl get pvc -A | grep -v Bound                                  # claims that are not Bound
kubectl get pv | grep -E "Released|Failed"                          # leftovers (Retain keeps data on purpose)
kubectl get pv | grep -c synology                                   # iSCSI LUN count; the NAS ceiling is ~10
kubectl get sc                                                      # local-path, nfs-client, synology-csi-iscsi-retain-vol2; NO default
kubectl -n synology-csi get pods -o wide                            # CSI controller (single replica) + node plugins
kubectl -n <ns> describe pvc <name> | tail -15                      # events say why it is Pending
kubectl -n <ns> describe pod <pod> | tail -25                       # mount errors are here
kubectl get volumeattachment | grep -v "true"                       # attach problems
```

Which tier is the volume on? `kubectl get pvc -n <ns> <name> -o jsonpath='{.spec.storageClassName}{"\n"}'`.

---

## 2. A new iSCSI claim fails: `Number of LUN reach limit`

**Cause:** the NAS allows about 10 iSCSI LUNs in total, across every volume; a hard model limit, not raisable. **Fix: do not use iSCSI.** Put the workload on the right tier (`nfs-client` for file-class, `local-path` for replicated or refillable data, emptyDir for caches). Only block-critical single-instance memory-mapped stores (LMDB, BoltDB) belong on iSCSI, and there is one today. Check the count with `kubectl get pv | grep -c synology`; at 10 you are at the wall. Never "fix" it by editing DSM configuration.

## 3. Pods hang in `Init:0/1` or `ContainerCreating` cluster-wide

**Cause:** the Synology CSI controller (a single-replica StatefulSet) is down, so every attach stalls.

```bash
kubectl -n synology-csi get pods -o wide
kubectl -n synology-csi logs synology-csi-controller-0 --all-containers --tail=50
kubectl -n synology-csi delete pod synology-csi-controller-0        # recreate it (the node plugins replace themselves)
```

After a **chart bump**, if the controller or a node plugin is in `ImagePullBackOff` with `synology/synology-csi:vX: not found`: the chart's `appVersion` points at a driver tag that was never published. Revert the chart pin in Git (**Landing a change**), let Flux roll back (about 5 minutes), then delete `synology-csi-controller-0` as above. Existing volumes keep working (sessions are kernel-held); nothing can attach or provision until it is fixed.

## 4. `non-retryable iSCSI login failure` / `iscsi_limit_max_session_count`

**Cause:** iSCSI LUNs are single-session. After an ungraceful restart, a stale session or discovery record pins a LUN to the wrong node.

```bash
W=<worker-ip>                                                       # e.g. 10.0.21.22
$SSH ansible@$W 'sudo iscsiadm -m session; echo ---; sudo iscsiadm -m node'
# A node record with no session whose PV is bound to a DIFFERENT node is an orphan. Delete it:
$SSH ansible@$W 'sudo iscsiadm -m node -T <iqn> -p 10.0.254.20 --logout; sudo iscsiadm -m node -T <iqn> -p 10.0.254.20 -o delete'
```

This recurs after **every** iSCSI volume migration between workers (the source keeps stale records; a later iscsid restart or reboot logs in to them and causes Multi-Attach errors elsewhere). A debounced timer on every worker (`iscsi-orphan-prune`, every 30 minutes, deletes a record only after it has been session-less for an hour) cleans these up on its own; to trigger it or re-install it: `ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <worker> --tags iscsi-orphan-prune`. Also check the NAS side: DSM → SAN Manager → Target → Connected Initiators; disconnect stale entries manually. Released PVs under `Retain` leave orphan sessions on the last worker: same logout/delete, then remove the LUN in DSM (a manual NAS step; `kubectl delete pv` does **not** delete the LUN).

## 5. A volume turned read-only (ext4 journal abort)

**Cause:** a network blip let the iSCSI session time out; ext4 remounted read-only.

```bash
kubectl -n <ns> scale statefulset/<name> --replicas=0               # stop the writer (deploy/<name> for a Deployment)
# Find the block device on the worker that held it
$SSH ansible@<worker-ip> 'sudo iscsiadm -m session -P3 | grep -E "Target:|Attached scsi disk"'
# fsck from Alpine: the RHEL 9 e2fsprogs is too old for the filesystem features this driver uses
kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh
#   inside: apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX && exit
kubectl -n <ns> scale statefulset/<name> --replicas=1
```

Confirm the right `/dev/sdX` before running fsck (device letters change across reboots). Stop and escalate if fsck reports heavy damage.

## 6. First mount of a big iSCSI volume is "stuck"

`MountVolume.MountDevice failed … DeadlineExceeded` on a brand-new LUN: the first format is bound by the NAS's TRIM, about 10 GB a minute (a 50 GiB volume takes ~5 minutes). Wait. Proof it is working, on the worker: `cat /sys/block/sdX/stat` (field 14, sectors discarded, keeps growing). If `mke2fs … apparently in use by the system` appears once, retry before reaching for `wipefs -a /dev/sdX`.

## 7. A non-root pod cannot write to an iSCSI (or NFS) volume

The Synology CSI driver does not honour `fsGroup`. The workload needs a short root init container that runs `chown -R <uid>:<gid> /mountpath` and **keeps default capabilities** (`capabilities.drop: [ALL]` strips `CAP_CHOWN` and the chown fails with `Operation not permitted`). This is a manifest change: land it through Git.

## 8. A stateful pod hangs in `Terminating` on a tainted control plane

**Cause:** the control-plane taint evicted the CSI node plugin; the kubelet cannot unmount; the `VolumeAttachment` stays attached to a dead node and the pod is Multi-Attach-blocked elsewhere.

```bash
kubectl get volumeattachment | grep <node>
# On the node (via a debug pod or SSH): log out the iSCSI session, then
kubectl delete pod <pod> --grace-period=0 --force
kubectl delete volumeattachment <name>
```

Prevention: drain stateful workloads **before** tainting a node.

## 9. `local-path` claim Pending, or pod cannot start after a node loss

```bash
kubectl -n <ns> describe pvc <name> | tail                          # "WaitForFirstConsumer" is normal until a pod uses it
kubectl get pv <pv> -o jsonpath='{.spec.nodeAffinity}{"\n"}'        # which worker is the volume pinned to?
$SSH ansible@<worker-ip> 'lsblk -f | grep -E "sd|xfs"; mount | grep " /data "; ls -ld /data; df -h /data'
$SSH ansible@<worker-ip> 'ls -l /dev/disk/by-path/ | grep "scsi-0:0:0:1"'     # the data disk by its STABLE address
```

- **`mkdir … Permission denied` in the provisioner events:** SELinux. `/data` must be mounted with `context="system_u:object_r:container_file_t:s0"`. The `local-path-disk` Ansible role does this: re-run `ansible-playbook -i inventory/hosts.yml playbooks/asgard-k3s.yml --limit <worker> --tags local-path-disk` from Frigg.
- **Never identify the disk by `/dev/sdX`.** Letters swap across reboots (and an iSCSI LUN can take the letter). Always `/dev/disk/by-path/*-scsi-0:0:0:1` or the UUID in fstab.
- **A `local-path` pod will not reschedule after the worker died:** the PV is node-pinned. It is down until the VM is restored (PBS backs up `/data`) or rebuilt (**K3s node rebuild**). Surface this as a known redundancy trade, not a surprise.
- A single-instance pod stuck on an old template (`UpdateRevision != CurrentRevision`, old pod CrashLooping): `kubectl delete pod <pod> --grace-period=0 --force`.

## 10. NFS-backed apps (Immich, MicroBin, Teamspeak files, media)

```bash
showmount -e 10.0.254.20 2>&1 | head                                 # the exports the NAS offers (from a client with showmount)
$SSH ansible@<worker-ip> 'mount | grep nfs; df -h | grep nfs'
kubectl -n <ns> describe pod <pod> | grep -iE "nfs|mount|stale"
```

- **`stale file handle`:** the export path changed (moving a shared folder between volumes changes `/volume1/x` to `/volumeN/x`). Confirm with `showmount -e`, fix the mount or PV path, then on the client `umount -f -l <mnt>; mount <mnt>`. Restore-verify a moved datastore before trusting it. PBS can show `active` against an unmounted datastore: mount, then `systemctl restart proxmox-backup proxmox-backup-proxy`.
- Sizes on `nfs-client` volumes are advisory; the DSM volume is the real ceiling. Check it in DSM → Storage Manager.
- **Never put SQLite or a memory-mapped engine on NFS.** Jellyfin's `/var/lib/jellyfin`, VictoriaMetrics, VictoriaLogs and Postgres do not belong there.

## 11. Disk pressure on a node

```bash
kubectl describe node <node> | grep -A6 Conditions
$SSH ansible@<node-ip> 'df -h / /var /data 2>/dev/null; sudo du -xh /var/lib/rancher --max-depth=2 2>/dev/null | sort -h | tail -5'
$SSH ansible@<node-ip> 'sudo /usr/local/bin/k3s crictl images | head -5; sudo /usr/local/bin/k3s crictl rmi --prune'      # remove unused images (safe). Full path: sudo's PATH lacks /usr/local/bin
kubectl get pods -A | grep -E "Evicted|ContainerStatusUnknown" | awk '{print $1,$2}' | xargs -n2 kubectl delete pod -n    # clear evicted pods
```

A control plane whose `/var/log/messages` is huge while a peer is down: etcd Raft-drop spam. An rsyslog rule drops that line (`k3s_logging` tag). To reclaim: delete old `/var/log/messages-*` files, then `sudo logrotate -f /etc/logrotate.d/rsyslog`. Check disks before any K3s upgrade hop.

## 12. Moving data between tiers (migrations)

Use quiesce → migrator pod → rsync → static rebind → recreate the StatefulSet. Rules that bit before: `rsync -aH --numeric-ids` (no `-A`/`-X`: NFS and the SELinux-mounted disk store no per-file labels); verify with a **content checksum**, not `du`; pin a migrator pod for a `WaitForFirstConsumer` volume with `nodeSelector` (never `nodeName`); reconcile the Flux source to the new manifest **before** deleting and recreating the StatefulSet, or it comes back on the old template. This is a planned change: **escalate** and use `docs/procedures/storage-tiering.md` in the repository.

---

## See also

- **Storage** (Hardware) and **Storage & data** (Components).
- **K3s node rebuild** (Procedures): iSCSI cleanup when replacing a worker.
- **Kubernetes & Flux** (this section).
