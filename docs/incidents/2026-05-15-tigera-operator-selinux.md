<!-- docs/incidents/2026-05-15-tigera-operator-selinux.md -->

### 2026-05-15 — tigera-operator SELinux fix

Carry-over from the 2026-05-14 incident. The operator was failing every reconcile with `open /var/lib/calico/mtu: permission denied`, leaving the CNI functional but unmanaged.

**Diagnosis:**
- `ausearch` returned nothing — the denial is `dontaudit`'d by default policy.
- `semodule -DB` (disable dontaudit, keep enforcing) revealed the AVC: `scontext=...container_t:s0:c322,c902 tcontext=...container_var_lib_t:s0` — **MCS category mismatch**. The operator has categories (normal container); the file is written by privileged calico-node with no categories. MCS dominance fails on read.
- This is upstream bug **projectcalico/calico#7851**, open since July 2023, never fixed. Tigera maintainer's recommended workaround: set MTU explicitly so the operator never needs to read the file.

**Fix shipped:** Added `mtu: 1450` to `spec.calicoNetwork` in `ansible/roles/k3s/templates/calico-installation.yaml.j2`. 1450 = 1500 (host MTU) - 50 (VXLAN overhead). After playbook re-run, the `Degraded` condition cleared and the operator stopped logging MTU read errors.

**Why not a SELinux policy module:** Would have meant authoring/maintaining a custom policy artifact per node, deploying via `semodule -i`, owning forever. The upstream-recommended workaround eliminates the need to read the file at all — cleaner posture, smaller surface, captured in IaC as a 3-line template change. Considered carefully (was nearly the chosen path until the upstream issue was found); MTU-explicit is the right call.

**`semodule -DB` discipline:** Re-enabled dontaudit (`semodule -B`) after diagnosis. Never leave dontaudit disabled — it floods audit log with noise the policy authors intentionally suppress.

