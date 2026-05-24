<!-- docs/incidents/2026-05-14-etcd-storm-cascade.md -->

### 2026-05-14 — etcd storm cascade (≈15h)

**Trigger:** Adding a second NIC (VLAN 20, for MetalLB) to the worker VMs and the resulting reboot of all worker nodes. The reboot, combined with Göndul's etcd member running on the underpowered Urd (N5095), caused an etcd IO storm.

**What it exposed (all latent, all pre-existing — the storm just made them fire at once):**
- **Calico `firstFound` autodetection** bound the overlay to the workers' new eth1 (VLAN 20) instead of eth0 (VLAN 21). Broke cross-node vxlan. → Fixed by pinning `nodeAddressAutodetectionV4` to `cidrs: ["10.0.21.0/24"]`. (The K3s addon controller *merged* the CR, leaving a stale `firstFound` alongside the new `cidrs` — had to be stripped with `kubectl patch`.)
- **Broken overlay → ESO admission webhook unreachable** from the API server → `infrastructure-config` Flux Kustomization stuck failing dry-run.
- **iSCSI session chaos** — ungraceful reboots left stale sessions/node records; one LUN got pinned to a CP node (the Synology CSI node plugin is a DaemonSet with no CP taint, so it runs on CP nodes). Vault pods stuck `Init:0/1` unable to mount. The MGMT-subnet doc error (`10.0.1.x` vs real `10.0.254.x`) nearly caused the *correct* iSCSI portal address to be "fixed".
- **Strict rp_filter** (set by the hardening role) silently dropped MetalLB LoadBalancer traffic arriving on the multi-homed workers' eth1. → Fixed by setting `rp_filter=2` (loose).
- **MetalLB L2 election** could pick a CP node (no eth1) and announce nowhere. → Fixed with `nodeSelectors` excluding CP nodes.
- **tigera-operator SELinux denial** on `/var/lib/calico/mtu` — surfaced during diagnosis. Resolved next day (see 2026-05-15 below).

**Resolution:** Overlay pinned to VLAN 21, rp_filter loosened, MetalLB nodeSelectors added, iSCSI sessions cleared, Vault recovered (3/3, KMS auto-unseal), etcd healthy, Flux reconciling. Cluster stable end of day.

**Root-cause pattern:** every failure was config that predated the workers' second NIC (or predated the current topology) and was never reconciled with it. Lens for the Göndul reprovision: *what here assumes a single NIC, or one role per node?*

**Fixes committed:** Ansible — Calico template (`firstFound`→`cidrs`), hardening role (`rp_filter` 1→2). Terraform — worker VLAN 20 NIC, CP VM resize to 1vCPU/2GB.

