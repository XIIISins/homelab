# Homelab — Claude Code context
*Read this before touching anything. Full design at `docs/homelab-design.md`.*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes. Goals:
- Reliable services for friends/family (Teamspeak, Factorio, Immich)
- Learning environment for Kubernetes and modern infrastructure
- Portfolio/resume project at senior/principal infrastructure level

Owner has 10+ years Ansible experience, career in IT/sysadmin/platform engineering. Kubernetes is the primary learning goal.

---

## Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Lexar NM790 (Gen 4, DRAM-less, HMB 3.0) | **Urd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Samsung 970 EVO (Gen 3, DRAM) | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB | 512GB NVMe SK Hynix PC300 (Gen 3, DRAM) | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

**Hardware notes:**
- **Urd hardware migration, 2026-05-19 to 2026-05-21:** Migration trail was DeskMini JB95 → NUC7 (intermediate) → MSI Cubi, not a direct swap. NUC7 was a brief stop that passed a 24h burn-in but proved hardware-flaky once running production Proxmox workload — crashed three times during einherjar-urd's life on it (visible as phantom "still running" entries in `last reboot` on einherjar-urd). Memory + disk were physically swapped from NUC7 into the Cubi. Final hardware is the MSI Cubi (i3-1215u + 1TB Lexar NM790 NVMe + reused 32GB DDR4). The 2026-05-14 etcd-storm root cause (slow N5095 + mSATA fsync latency) **no longer exists on this hardware**. The "never run CP on Urd" rule from that incident is retired. Phase 4b (Göndul Verd → Urd migration) is the deliberate follow-up — deferred to a separate session, not opportunistic. Current CP topology stays: Göndul + Hlökk on Verd, Sigrún on Skuld. **Collateral from the NUC7 crashes:** one of them (around 2026-05-20 07:00) corrupted einherjar-urd's SSH hostkey files mid-write — see "Hostkey zero-byte after crash mid-write" in Known gotchas. Discovered 2026-05-21 evening during Phase 4a cleanup.
- **Verd hardware migration, 2026-05-23 (Phase 4c):** Beelink MINI-S12 (N100, 16GB) → MSI Cubi (i3-1215u, 32GB). Same-node refresh: SSD transplanted, hostname/identity/IQN preserved. Procedure: live-migrate VMs/LXCs off Verd → shutdown → SSD transplant → boot new Cubi → fix NIC name → OS updates + reboot test → migrate workloads back. **No workload-affecting downtime** — cluster stayed 3/3 throughout. Only surface was NIC rename `nic0` (Beelink UEFI-labeled) → `enp45s0` (Cubi predictable-naming), fixed at console with vim `:%s/nic0/enp45s0/g` in `/etc/network/interfaces` + `ifreload -a`. Both Cubi nodes now identical hardware (i3-1215u / 32 GB DDR4); Skuld is the N100/16GB outlier. The NIC-rename class is documented as a Known gotcha — silently hit during the Urd refresh too.
- **Storage tier picture (verified 2026-05-21 via `nvme id-ctrl`):** Urd has Lexar NM790 (Gen 4, DRAM-less, HMB 3.0). Verd has Samsung 970 EVO 1TB (Gen 3, DRAM-equipped). Skuld has SK Hynix PC300 512GB (Gen 3, DRAM-equipped — `hmpre=0, hmmin=0` confirmed direct DRAM, not HMB). For etcd fsync consistency: Verd ≈ Skuld > Urd. Urd's DRAM-less NVMe is the slowest of the three for sustained sync workloads even though it's Gen 4 — HMB borrows host RAM but adds PCIe round-trip latency per metadata update. Still vastly better than the old mSATA — fine for everything except being the busiest etcd member. Storage tier informs Phase 4b: when Göndul moves to Urd, it becomes the slowest-disk CP, but all three are well within etcd's fsync tolerance.
- Urd long-term plan: dedicated Jellyfin box with Intel QuickSync passthrough. The i3-1215u has UHD Graphics — significantly stronger QSV than the N5095's UHD. Currently runs Einherjar-urd (K3s worker) and the Factorio LXC (1120).
- Göndul moved Urd → Verd on 2026-05-17 (was deferred since the 2026-05-14 incident; fixed during the asgard rebuild). Phase 4b (2026-05-22) returned Göndul to Urd post-hardware-refresh — back to the original 2026-05-14 design intent, this time on hardware that handles etcd fsync properly.
- All three CPs (Göndul/Hlökk/Sigrún) bumped 1vCPU/2GB → **2vCPU/4GB** on 2026-05-17 during the Authentik deploy. Symmetric 2vCPU/4GB across all three is now the standard (CP sizing identical across nodes, same rule as PG nodes — failover symmetry requires it).
- **CP-only workload posture (Phase 4a, applied 2026-05-21):** All three CPs are tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Workload pods cannot land on them. Synology CSI node-plugin DaemonSet was evicted from CPs as expected. K3s-shipped components (CoreDNS, metrics-server, calico-apiserver) and Calico-node, kube-proxy, metallb-speaker (DaemonSets that tolerate the taint by design) remain. 4 GiB is the correct CP size with this posture — control-plane-only working set is ~1.5-2 GiB. **Important caveat surfaced during deploy:** evicting CSI from CPs is *not* a free win — see Known gotchas "CSI eviction footgun". Drain stateful workloads from a CP *before* applying the taint, or the kubelet's unmount path breaks.
- **2026-05-14 incident context:** An etcd IO storm on Göndul (Urd/N5095) cascaded into a multi-hour outage. The 2026-05-21 hardware refresh removes the root cause; the structural lessons (per-component config Kustomizations, idempotency, sub-kustomization-per-component) stay. See `homelab-design.md` incident log.

---

## Naming convention

| Thing | Name | Reason |
|-------|------|--------|
| Proxmox cluster | `niflheim` | Hidden realm |
| Proxmox node 1 | Urd | Eldest Norn, most powerful |
| Proxmox node 2 | Verd | Second Norn |
| Proxmox node 3 | Skuld | Third Norn |
| NAS | Munin | Odin's raven of memory |
| Asgard K3s CP | Göndul, Hlökk, Sigrún | Valkyries (choosers/directors) |
| Asgard K3s workers | Einherjar-urd/verd/skuld | Army of the Norns |
| Jotunheim K3s CP | Rota, Hildr, Kára | Valkyries |
| Jotunheim K3s workers | Drengr-urd/verd/skuld | Heroes of the Norns |
| AdGuard primary | Saga | Goddess of wisdom/seeing |
| AdGuard replica 1 | Mimir | Keeper of wisdom |
| AdGuard replica 2 | Kvasir | Wisest being |
| Asgard PostgreSQL | Fulla, Vör, Idunn | Frigg's handmaidens + Idunn the keeper. Meta-principle: primary defines theme, replicas expand within it. |
| External (public) zone | `xiiisins.com` (apex) | Cloudflare-resolved — services exposed via Cloudflared land here (e.g. `authentik.xiiisins.com`) |
| Internal alias zone | `midgard.xiiisins.com` | The known world — AdGuard-resolved internal alias for services that ARE publicly reachable (lets homelab clients hit them via LAN instead of trombonning through Cloudflare) |
| Internal-only zone | `niflheim.xiiisins.com` | The hidden realm — AdGuard-resolved, never publicly reachable |

---

## Critical architectural decisions — never second-guess without asking

**Two K3s clusters:**
- Asgard K3s (VLAN 21) — core infrastructure + automation + production services. Cascade-failure criterion *or* recovery-blocking criterion. Resiliency > simplicity.
- Jotunheim K3s (VLAN 31) — non-cascade-critical services + genuine experiments. Failure acceptable (hours-to-days downtime). NOT "experimental only" — most jotunheim services have real users; the line is failure-domain risk, not production/experimental.
- Do NOT suggest merging them

**Asgard K3s contents:**
- *Core infrastructure:* Vault, Authentik (server ×3 + worker ×1), Redis, MetalLB, Synology CSI, ESO, Sealed Secrets, tigera-operator, Traefik, cert-manager, Cloudflared
- *Automation:* AWX (Ansible CI), Tofu Controller (Terraform GitOps via Flux)
- *Core services:* Outline, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Netbox, n8n, Privatebin, Startpage

**Jotunheim K3s contents:** Arr stack, Komga, Homepage, wallpaper gallery, second instances of ESO and Synology CSI, plus ad-hoc experiments. Note: Startpage (personal browser homepage, daily-use) is in asgard; Homepage (service-grid dashboard) is in jotunheim — distinct services.

**No Docker Swarm. K3s only.**

**No Galera. PostgreSQL only.**
Zabbix migrated to PostgreSQL. Nothing requires MySQL.

**GitOps: Flux CD.**
Push to Git → exists. No ArgoCD. No manual `kubectl apply` for production.
Layered Flux Kustomizations: `infrastructure` installs operators/charts; per-component config Kustomizations (`infrastructure-config` for ESO, `metallb-config` for MetalLB, `vault-config` for the vault-unseal SealedSecret, `synology-csi-config` for the synology-csi SealedSecret) configure CRD-dependent resources with `dependsOn: infrastructure` so the CRDs exist first. Add a new config Kustomization per operator rather than bundling — shared bundles share failure domains (an ESO webhook failure blocked MetalLB config reconcile during the 2026-05-14 incident; mixing SealedSecrets into `infrastructure/` itself caused the asgard rebuild to fail on CRD timing). The naming convention is `<component>-config/`.

**Calico CNI is NOT Flux-managed.**
Calico is installed as a K3s auto-deploy addon: the Tigera operator manifest plus an `Installation` CR templated by Ansible to `/var/lib/rancher/k3s/server/manifests/calico-installation.yaml` (source: `ansible/roles/k3s/templates/calico-installation.yaml.j2`, applied by `ansible/roles/k3s/tasks/calico.yml`, runs only on `k3s_init_node`). The K3s addon controller applies it; the Tigera operator acts on it. To change Calico config, edit the Ansible template and re-run the playbook — `kubectl edit` on the live `Installation` gets reverted by the addon controller.

**DNS: AdGuard Home (not Pi-hole).**
Three LXCs, keepalived VIP at `10.0.10.200`. AGH Sync binary on Saga. Do not suggest Pi-hole.

**DNS zones — three-zone scheme.**
- `xiiisins.com` (apex) — external/public. Resolved by Cloudflare. Externally-exposed services live here (e.g. `authentik.xiiisins.com`). Traffic enters via Cloudflared.
- `midgard.xiiisins.com` — internal alias for services that ARE publicly reachable. Resolved by AdGuard. Same services as the apex but resolved to Traefik VIP `10.0.20.10` directly — homelab clients hit them via LAN instead of trombonning through Cloudflare.
- `niflheim.xiiisins.com` — internal-only services that are NEVER publicly reachable. Resolved by AdGuard.

Cert strategy follows the zones:
- `*.niflheim.xiiisins.com` wildcard — issued in 5e.1, covers all internal-only services.
- `*.midgard.xiiisins.com` wildcard — added in 5e.2, covers internal aliases of publicly-reachable services.
- `*.xiiisins.com` apex wildcard — added in 5e.2, used on the origin side of Cloudflared (Traefik → Cloudflared). Browser-visible cert for external traffic is Cloudflare's universal cert; Cloudflare Tunnel terminates TLS at the edge with Cloudflare's cert and re-encrypts to origin where our wildcard lives.

All three wildcards via cert-manager DNS-01 against the same Cloudflare zone-scoped token (`secret/k8s/cert-manager/cloudflare`, scope: `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`).

**Identity: Authentik in asgard K3s.**
OIDC for web apps, LDAP for SSH via SSSD. Local admin accounts on all services as break-glass. Do not suggest Authelia.

**Secrets: two access patterns, three stores.**
- **1Password "Homelab" vault** (humans) — anything a human looks up: web admin passwords, API tokens pasted into configs, LXC root passwords for templates, homelab-hosted DB admin credentials, AppRole credentials for the MacBook control node. Also break-glass user keys and AWS KMS unseal token (already external by design).
- **HashiCorp Vault (asgard K3s, runtime)** — anything a machine pulls at runtime: K8s workload secrets via ESO, Ansible role lookups via AppRole, future runtime-retrieval targets. 3-node Raft HA, AWS KMS auto-unseal, iSCSI storage (Synology CSI, 5Gi PVC, `synology-csi-iscsi-retain`).
- **Ansible Vault (machines, bootstrap)** — secrets needed *before* HashiCorp Vault is reachable: `k3s_token`, RHEL subscription keys, SSH public keys for ansible/break-glass users. Narrow scope by design — only what's required to bootstrap a fresh node up to the point where HashiCorp Vault becomes usable.

The rule: *Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

The bootstrap-vs-runtime split solves the circular dependency: HashiCorp Vault lives in asgard K3s, so anything K3s itself needs to come up cannot live there.

Scope of homelab secret stores: "things that exist because the homelab exists." Personal credentials, external service accounts, and infrastructure under the homelab (bare-metal node root passwords, NAS admin, UCG-Ultra, KPN router) live in 1Password but outside the Homelab vault — they're personal/external, not homelab.

Vault Kubernetes auth method, `eso` policy, `eso` role, AppRole auth method, `ansible` policy, and the `ansible-local`/`ansible-awx` AppRole roles captured in Terraform (`terraform/vault/`) on 2026-05-16. SecretIDs are NEVER in Terraform state — generated manually, stored in 1Password + local env file. See homelab-design.md AppRole bootstrap runbook.

Vault listener is plaintext (`tls_disable = 1`) — deliberate, see Known gotchas.

**Ansible Vault: bootstrap secrets only.**
Holds `k3s_token`, RHEL subscription keys, SSH pubkeys for ansible/breakglass users — the minimum needed to bring a fresh node up to the point HashiCorp Vault is reachable. All other Ansible-pulled secrets live in HashiCorp Vault under `secret/ansible/*`, retrieved via the `community.hashi_vault` lookup with AppRole auth. Proof-of-pattern: `ansible/playbooks/test-vault-lookup.yml` — minimal `lookup('community.hashi_vault.vault_kv2_get', '<path>').secret.<field>` form; auth via `ansible-vault-env` fish function sourcing `~/.config/ansible/vault-approle.env` into the environment (no explicit `url=` / `role_id=` / `secret_id=` args on the lookup). (Earlier versions of this line cited `secret/ansible/sftpgo/admin-password` as the proof-of-pattern, but the SFTPGo role still does local `pwgen` + on-disk `/etc/sftpgo/admin-password.txt` — pending Vault migration tracked in homelab-design.md.)

**Jellyfin: privileged LXC on Urd.**
Intel QuickSync via /dev/dri passthrough. Not in K3s.

**Storage: Synology CSI driver (christian-schlichtherle/synology-csi-chart).**
iSCSI only — NFS creates polluting shared folders on Synology. Single StorageClass: `synology-csi-iscsi-retain` (default). Two instances planned — one per K3s cluster. Not democratic-csi.
Synology CSI creates one iSCSI target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). iSCSI LUNs are single-session by default — after ungraceful node restarts, stale sessions / discovery node records can pin a LUN to the wrong node and block re-attach. Cleanup: `iscsiadm -m session` / `iscsiadm -m node -o delete` on the affected node.
NOTE: there is a vestigial iSCSI target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` left over from an abandoned NFS-CSI attempt — it is NOT in use. The live targets are per-PVC.

**MetalLB: L2 mode.**
Workers have two NICs: eth0 (VLAN 21, K3s traffic) and eth1 (VLAN 20, MetalLB L2 advertisement).
Worker eth1 IPs: 10.0.20.201/202/203 (outside MetalLB pool 10.0.20.11-.99).
L2Advertisement restricted to eth1 interface AND restricted to non-CP nodes via `nodeSelectors` (`matchExpressions` — key `node-role.kubernetes.io/control-plane`, operator `DoesNotExist`). CP nodes have no eth1; without the nodeSelector, MetalLB's L2 election can pick a CP node and announce nowhere.
Multi-homed-worker plumbing required for VIPs to be reachable from outside the cluster, all in `roles/k3s/tasks/network.yml`:
- `rp_filter=2` (loose) on `all` and `default` — strict mode drops MetalLB traffic on eth1
- `route_localnet=1` on `all` — without this the kernel drops packets destined for the VIP because MetalLB doesn't bind the IP, only ARPs for it
- Source-based policy routing — a systemd oneshot service (`vlan20-policy-routing.service`) installs `from 10.0.20.0/24 lookup vlan20` rule + a default route via 10.0.20.1 in table `vlan20`, so replies originating from the worker's eth1 IP exit via eth1 (symmetric path) rather than the default eth0 route (asymmetric, dropped by UCG's stateful firewall)

**Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs/Grafana (jotunheim K3s).**
Zabbix stays as LXC for monitoring independence. VictoriaLogs replaces Loki. VictoriaMetrics replaces Prometheus.

**Ansible: AWX in jotunheim K3s.**
30-minute scheduled reconciliation. Vault-backed credentials.

**VM disks: local LVM-thin.**
Faster than NFS at 1 GbE.

**PBS: privileged LXC on Skuld.**
NFS bind-mounted via Proxmox host.

**Repo: private** (GitHub). Secrets never in Git regardless; SealedSecrets used for bootstrap secrets.

**Internet exposure: KPN Experia Box → UCG-Ultra DMZ.**
KPN configured as "exposed host" / DMZ pointing all unsolicited inbound (IPv4 *and* IPv6) at the UCG-Ultra's WAN IP. UCG-Ultra is the sole firewall policy boundary; KPN does outbound NAT for `192.168.2.0/24` devices (settop box, family devices) only. UCG firewall posture: `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). Port-forwards configured on UCG only. KPN is the one piece of infra that is NOT and will NEVER be in IaC — any change to it lives here in the docs or it doesn't exist.

**LXC management is via Terraform `asgard-lxcs` module.**
Same provider (`bpg/proxmox ~> 0.77`), same `terraform.tfvars` as `asgard-k3s/` (API token + SSH pubkey), parallel structure. To add a new asgard LXC: append to `lxcs.tf`, `terraform apply`, add the host to `inventory/hosts.yml`, write the role + playbook.

**LXC bootstrap flow.**
Day 1:
1. `terraform apply` (LXC has only root SSH, with the key from `var.ssh_public_key`)
2. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml -e 'ansible_user=root' --tags baseline` (smallest root surface)
3. `ansible-playbook -i inventory/hosts.yml playbooks/<service>-host.yml` (full deploy as `ansible`, hardening locks root SSH out at the end)

Day N: just step 3. Root SSH is locked out via `AllowUsers ansible recovery`. If something breaks the ansible user, recovery is your way in (key in 1Password).

**Factorio LXC: operator self-service via SFTPGo + reconcile loop.**
LXC 1120 hosts Factorio + SFTPGo on the same host. The operator never gets shell access — they SFTP into `/factorio/` and edit JSON control files. A root-owned Python reconcile script runs as a systemd timer every 30s, reading desired state from `/factorio/control/factorio-control.json` and converging actual state (version installed, service running/stopped, restarts). The operator declares intent; the reconciler owns mechanism. This is the template pattern for future operator-managed game/voice services.

---

## Working with this repo — process expectations

These are procedural instructions for Claude. Follow them on every session before drafting plans or implementations.

### Before proposing any new work — pre-flight checklist

When the owner says "let's deploy X" / "what's next?" / "let's plan Y," do this first, in order:

1. **Search the docs for X.** What does the design doc already say about X? Has any decision been made? Are there constraints listed?

2. **Scan the open questions / pending tasks section of `homelab-design.md`.** Specifically check:
   - Does X depend on any unchecked task? (Architecturally — would deploying X be wrong, fragile, or knowingly-broken without that task being done first?)
   - Does X interact with any unchecked task? (Would the new workload make a latent issue fire? Does it touch the same component/node/system?)
   - Does X make any pending task more urgent? (Was this item "later" because nothing exercised the gap — and is X exactly the thing that would exercise it?)

3. **Scan known gotchas for X and adjacent systems.** Not just "is there a gotcha for X" but "what gotchas hit systems X depends on" (storage class, secret store, networking, DNS, the consuming workload pattern, etc.).

4. **Treat pending tasks as prerequisites, not backlog.** The pending-tasks list documents architectural debt. The moment a new workload meaningfully exercises that debt, the relevant items get pulled forward — not left for later. Surface them explicitly: "before X, items A, B, C should be closed because they affect X in ways Y and Z."

5. **Propose the sequence with prerequisites first.** Don't draft Phase 1 of X if Phase 0 should be a pending-task closure. If a pending task is a clear prerequisite, name it as Phase 0 (or 4a, or whatever fits the existing numbering) and explain *why* it's a prerequisite rather than nice-to-have.

If steps 1–4 turn up nothing concerning, then draft the plan for X. If they turn up something, that becomes the proposed first phase before X itself.

This is the lens that should have caught the 2026-05-17 evening CP-taint miss: the pending-task `node-role.kubernetes.io/control-plane:NoSchedule` was listed for weeks, the Authentik deploy was exactly the workload that would exercise the gap it left, and the prerequisite was missed. Tonight's incident is the canonical example of why this checklist exists.

### After completing any work — post-flight checklist

When work lands successfully:

1. **Update the design doc and CLAUDE.md.** Both have specific structures that need to stay in sync:
   - `homelab-design.md` build sequence: tick the phase, add any sub-phases that emerged
   - `homelab-design.md` decision log: add rows for new architectural decisions made during the work
   - `homelab-design.md` incident log: if the work was non-trivial (multiple findings, surprises, recovery steps), add an entry with the findings list
   - `homelab-design.md` open questions: mark closed items, add new items surfaced by the work
   - `CLAUDE.md` current build status: update the relevant ✅/🔲 line
   - `CLAUDE.md` known gotchas: add every new gotcha class discovered, with enough context that future-Claude understands why the rule exists, not just what the rule is
   - `CLAUDE.md` hardware/VM/repo-structure sections: update if anything moved or got resized

2. **Choose the right delivery format — full file or patch.** The choice is signal-density vs friction. Rule of thumb:
   - **<30 lines changed across <5 hunks** → patch. Signal-dense, easy to read, easy to apply.
   - **>100 lines changed or restructuring sections** → full file output. Patch becomes hard to read at that scale; full file is also more robust to drift (no context-line mismatches).
   - **In between (30–100 lines)** → ask the owner which they prefer for this change. Default to full file if the change spans many sections; default to patch if it's contiguous edits in 1–2 sections.
   - When delivering a patch: use unified diff format, `-p1` paths (`a/` / `b/`), generate against the most current copy in `/mnt/project/`. Always run `patch --dry-run -p1` against a copy of the source before delivery to confirm it applies. Note in the delivery message that `/mnt/project/` is a snapshot and may have drifted — if the patch fails, fall through to the staleness rule below.
   - When delivering a full file: deliver via the file-creation/present-files mechanism, not inline in chat (avoid copyright limits, easier for owner to diff locally).

3. **Cross-reference between the docs.** Don't put a gotcha only in CLAUDE.md if it relates to a decision in homelab-design.md, or vice versa. Both docs should be navigable independently.

4. **Suggest the commit message.** Conventional commits style, concise subject line, body listing the actual changes if material. Reference the phase number if applicable.

5. **Name what's next.** Don't leave the owner to figure out "okay, what now?" — at the end of any significant work, propose the next step explicitly, applying the pre-flight checklist to that next step.

### Persistence validation — reboot-test before declaring done

After any change that needs to survive a reboot — physical hardware operations, persistent config edits (`/etc/network/interfaces`, `/etc/sysctl.d/`, systemd units, kernel/module changes), OS updates — reboot the affected node *before* re-loading workloads or marking the work complete. The window between "stable and working" and "back under production load" is the cheapest moment to confirm the change actually persists, not just that it took effect in memory.

Skip this and you're trusting that the on-disk config matches what's running. Common failure mode: runtime fix worked (e.g. `ip link set`, `sysctl -w`) but the on-disk file has a typo, wrong path, or syntax error — surfaces only at the next reboot, often days or weeks later when context is lost. Established 2026-05-23 during Phase 4c Verd refresh.

### When the owner pushes back

If the owner says "X should have happened differently" or "we missed Y" — acknowledge it directly, name the specific pattern that was missed, and propose how to catch it next time. Don't be defensive, don't over-apologize, don't promise "I'll do better." Name the lens that was missing and add it to this file if it's general enough to apply beyond the immediate case. The above checklists are the result of exactly this kind of feedback.

### Working with stale file snapshots — ask, don't speculate

`/mnt/project/` is a snapshot of the repo *at the moment Claude's session loaded it*. The owner edits between turns. When delivering patches (or making edits that depend on the current shape of a file), the snapshot can disagree with reality.

**Rule:** when a patch fails to apply, an `str_replace` would fail, or otherwise Claude is uncertain about the current shape of a file — **ask the owner for a grep**, don't speculate or regenerate against assumed state. A few lines of grep output is cheap; regenerating a patch against the wrong baseline wastes a turn and produces a wrong patch.

Concrete forms the ask can take:
- "Patch failed to apply at hunk N. Can you `grep -nA5 '<distinctive line>' <file>` so I can see the current shape?"
- "Before I edit, can you `sed -n 'X,Yp' <file>` to confirm what's there now?"
- "What does `head -100 <file>` look like right now?"

What this is NOT: an excuse to ask for the whole file. Targeted greps only. The owner shouldn't have to paste large blobs because Claude can't be bothered to figure out which 20 lines matter.

### Phase structure & doc separation

The implementation plan in `docs/homelab-design.md` is a route-to-done, not a runbook. Keep these structural rules in mind when proposing or executing work.

**Decomposition depth — routine work (default).** Max 3 levels.

- L1: phase number (`6`)
- L2: letter (`6a`, `6b`, `6c`, `6d`)
- L3: numeric (`6d1`, `6d2`)
- Beyond L3 → checkboxes inside the L3 step, not deeper numbering.

**Decomposition triggers.** Only sub-decompose when one of these is true:

- L2: genuinely distinct work-units with their own state / tools / retry granularity (e.g. Terraform VMs vs Ansible provisioning vs Flux deployment vs per-service rollout)
- L3: internal ordering or scope-choice within an L2 step that's worth pinning in the plan (e.g. "core required services" vs "wanted services" — distinct because the first gates the second)
- Don't sub-decompose because the work touches multiple files, crosses tool boundaries (Terraform → Ansible → kubectl), or just "feels big." If it's a sequence of mechanical steps with no real go/no-go between them, it's one step with checkboxes.

**Escape hatch — extremely complex work (exception).** For multi-week sequences, multi-component teardown-rebuild operations, or anything where 3 levels genuinely isn't enough, use the underscore form: `8j3_5.8.3`. The `_` is a deliberate signal that this is the complex tier. Default OFF — use only when the work genuinely spans multiple sessions and needs explicit inter-session state tracking.

**Existing too-deep structures stay as historical record.** Pre-rule structures (e.g. `5e.3.e.iv` — four levels of alternating letter/number) are not retroactively flattened. The rule applies prospectively from the next surfacing back to L3/L2/L1. Rewriting history is more churn than it's worth.

**Content separation — where things live.** Implementation plan, architecture, runbooks, and gotchas live in different places. Mixing them is what made `5e.3.e` bloat — implementation discoveries kept tempting mid-stream doc-patches because the plan and the reference notes shared the same surface.

- `docs/homelab-design.md` — design + status + decisions. Implementation plan rows are concise (one-line summaries with status tick). Architecture sections describe what something is and why, not how to deploy it.
- `docs/procedures/...` — step-by-step operational content. Composable (a "deploy a VM" procedure can reference a "Terraform deploy" procedure plus an "Ansible provision" procedure). Multi-day sequences go in `docs/procedures/runbook/`.
- `docs/known-issues/...` — gotchas drawn from incident log + post-flight discoveries. Duplication with CLAUDE.md's "Known gotchas" section is acceptable / expected (CLAUDE.md keeps gotchas in Claude's runtime context; `known-issues/` is the human-browseable canonical home).
- `CLAUDE.md` — process rules + Claude-coding context + gotchas (kept here for Claude's runtime context).
- Code-adjacent `README.md` (in role/module dir) — how the thing works and how to use it. Brief; for an Ansible role this can be as small as "add to your roles list," for a Terraform module a usage block + variables.

The `docs/` tree restructure (`docs/procedures/` + `docs/known-issues/`) is itself pending work, scheduled between Phase 5 and Phase 6 — tracked in homelab-design.md. Until the migration lands, runbook content continues to live in `docs/teardown-rebuild.md`-style files at the docs root, and gotcha discoveries continue going into CLAUDE.md's "Known gotchas" section.

**Mid-phase doc updates.** During phase execution, update `homelab-design.md` mid-stream only for:

- Decision row changes (the plan itself is shifting under us)
- Pending tasks surfacing that need explicit tracking before phase close
- Architectural reality diverging from what the plan assumed

Don't update mid-phase for:

- Implementation gotchas → batch into the post-flight bundle
- Findings that don't affect the active plan → batch
- Cosmetic / wording improvements → batch

Phase close gets one consolidated doc commit: status ticks, gotchas batch, pending tasks added, decision rows revised if needed.

### Boundaries on these checklists

- These are process rules, not rules-about-rules. They tell Claude how to *approach* work; they don't override domain-specific instructions in the rest of this doc.
- If the owner explicitly says "skip the pre-flight, just write the manifest" — skip it. They're the architect, not Claude.
- The pre-flight is a few-minutes-of-doc-search step, not a multi-turn interrogation. The output is "I checked these things; here's what I found" — not a list of clarifying questions.
- Pending tasks should be flagged as prerequisites when they're prerequisites. They should *not* be flagged when they're genuinely orthogonal to the proposed work. Use judgment.

---

## Network

> **MGMT subnet is `10.0.254.0/24`.** Earlier drafts of this doc said `10.0.1.0/24` — that was wrong and nearly caused a correct iSCSI/portal address to be "fixed". VLAN 1 = `10.0.254.0/24`.

```
KPN Experia Box (192.168.2.0/24, untouched) — DMZ → UCG-Ultra WAN
  └── UCG-Ultra WAN
        ├── LAN 1 → Dumb switch (your room)
        │             ├── MacBook dock (VLAN 60, 10.0.60.10 static)
        │             ├── Game PC (VLAN 60)
        │             └── Hue bridge (VLAN 60)
        └── LAN 2 → Dumb switch (spare bedroom)
                      ├── Urd   (10.0.254.11)
                      ├── Verd  (10.0.254.12)
                      ├── Skuld (10.0.254.13)
                      └── Munin (10.0.254.20)
```

### VLANs

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-WRK | Asgard K3s nodes |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-WRK | Jotunheim K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage/NFS (stable) |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

### Key IPs
- `10.0.254.1` — UCG-Ultra
- `10.0.254.11/12/13` — Urd/Verd/Skuld
- `10.0.254.20` — Munin (Synology)
- `10.0.10.200` — AdGuard VIP ✅
- `10.0.11.20` — PBS (LXC 1101) ✅
- `10.0.11.201/202/203` — Saga/Mimir/Kvasir ✅
- `10.0.21.11/12/13` — Asgard K3s CP (Göndul/Hlökk/Sigrún)
- `10.0.21.21/22/23` — Asgard K3s workers (Einherjar-urd/verd/skuld) — eth0
- `10.0.20.11–.99` — Asgard K3s MetalLB pool
- `10.0.20.201/202/203` — Worker eth1 IPs (Einherjar-urd/verd/skuld)
- `10.0.31.11/12/13` — Jotunheim K3s CP (Rota/Hildr/Kára)
- `10.0.31.21/22/23` — Jotunheim K3s workers
- `10.0.30.11–.99` — Jotunheim K3s MetalLB pool

### Cluster CIDRs
- Pod CIDR: `10.42.0.0/16` (`k3s_pod_cidr` — used for K3s `cluster-cidr` AND the Calico ipPool)
- Service CIDR: `10.43.0.0/16` (`k3s_service_cidr`)

### Resource IDs
- `1101–1199` — Asgard LXCs (sub-grouped by function)
- `2001–2999` — Asgard K3s VMs
- `3001–3999` — Jotunheim K3s VMs
- `10001+` — Templates

---

## Current build status

- ✅ UCG-Ultra — all VLANs, zones, firewall
- ✅ KPN DMZ → UCG-Ultra (IPv4 + IPv6)
- ✅ Synology (Munin) — factory reset, volumes, NFS, kubernetes user
- ✅ Proxmox cluster — Urd/Verd/Skuld on PVE 9.x, cluster niflheim
- ✅ PBS — LXC 1101 on Skuld, NFS datastore, connected to cluster
- 🟡 AdGuard Home — Saga/Mimir/Kvasir, keepalived VIP 10.0.10.200, adguardhome-sync. Functionally ✅ (DNS works, sync works) but **manually installed — no Ansible role, not in IaC**. Discovered as a gap during 5e.1.i cutover. Sync binary: `/usr/local/bin/adguardhome-sync` on Saga; systemd unit `adguardhome-sync.service`; config `/etc/adguardhome-sync.yaml`. Sync interval `*/30` was too long for operational tempo (5e.1 cutover surfaced this); manually bumped to `*/1`. **Phase 5b.2 — AdGuard IaC** pending: Terraform LXC module + Ansible roles for AGH + adguardhome-sync, validated via destroy-one-replica rebuild.
- ✅ Asgard K3s — fully IaC end-to-end. **Validated via full teardown+rebuild on 2026-05-17.** Surfaced and fixed: route_localnet sysctl, VLAN 20 policy routing, k3s role install-idempotency, vault-unseal + synology-csi SealedSecret CRD-timing split, Vault test KV entry as IaC, CP cpu/memory parameterization. Göndul moved to Verd at the same time.
- ✅ Sealed Secrets — deployed via Flux. **Master keys backed up to 1Password as of 2026-05-17.** Loss of these keys makes every SealedSecret in Git undecryptable.
- ✅ Synology CSI — iSCSI only, StorageClass synology-csi-iscsi-retain (default)
- ✅ Vault — 3 node Raft HA, AWS KMS auto-unseal, iSCSI storage, initialized; K8s auth method + KV engine + ESO policy/role + AppRole + 2 AppRole roles in Terraform (`terraform/vault/`). Root token + recovery keys in 1Password (post-rebuild, tagged `asgard-rebuild-2026-05-17`).
- ✅ External Secrets Operator — deployed; ClusterSecretStore `vault` Ready
- ✅ MetalLB — L2 working end-to-end. VIP `10.0.20.11` reachable from outside the cluster. Required nodeSelectors, Calico autodetection pin, rp_filter loose mode, route_localnet, and VLAN 20 policy routing — all in IaC as of 2026-05-17.
- ✅ tigera-operator — fixed 2026-05-15 via MTU explicit (workaround for upstream projectcalico/calico#7851). No longer Degraded.
- ✅ Factorio LXC (1120) — Deployed 2026-05-16. Terraform + Ansible end-to-end. Factorio headless + SFTPGo running. Operator self-service via SFTP on TCP 22022. Game on UDP 34197. Reachable at `factorio.xiiisins.com` (external) and `factorio.niflheim.xiiisins.com` (internal).
- ✅ Fulla / PostgreSQL 1 (LXC 1130) — Deployed 2026-05-17. PG 17 from PGDG on Skuld at 10.0.11.230. TLS (self-signed), scram-sha-256 only. Two SUPERUSER management roles (admin, ansible) with passwords in HashiCorp Vault. Per-service DB provisioning machinery validated by Authentik (2026-05-17 evening). Standalone; cluster expansion to Vör (1131, Urd) + Idunn (1132, Verd) + HAProxy VIP (10.0.10.210) deferred until further consumers exist.
- ✅ Authentik + Redis — Deployed 2026-05-17 evening. Authentik 2026.2.3 (3× server, 1× worker) + hand-rolled Redis StatefulSet (`redis:7-alpine`, single replica, AOF persistence on iSCSI). External Postgres pointed at Fulla. LoadBalancer at `10.0.20.12` (`authentik.niflheim.xiiisins.com`). All secrets via ESO from `secret/k8s/authentik/*` in Vault. Day-1 blueprints in Git: niflheim brand (demotes shipped `authentik-default`, claims default) + personal admin user. Bare-LB / plaintext-HTTP; Traefik + cert-manager land in Phase 4 to put it behind TLS.
- ✅ Phase 4a — CP workload isolation. Deployed 2026-05-21. All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Tainted via kubectl on existing nodes (the `node-taint:` config-template change registers the taint only at *new* node registration — existing nodes need explicit kubectl). K3s config templates (`config-init.j2` + `config-server.j2`) updated for future node bootstraps. Workload pods migrated to workers via natural churn + targeted `kubectl delete pod`. Vault HA distribution post-Phase 4a: vault-0 on einherjar-urd, vault-1 on einherjar-skuld, vault-2 on einherjar-verd. **MetalLB pinned 0.15.x** + **synology-csi pinned 0.11.1** during deploy — both `0.x` floating pins broke during the work (metallb 0.16 unconditional `prometheus.serviceMonitor` template, synology-csi 0.11.2's appVersion v1.3.0 image not published on Docker Hub).
- ✅ Urd hardware refresh — 2026-05-21. MSI Cubi replaces DeskMini JB95. i3-1215u + 1TB Lexar NM790 + reused 32GB DDR4. Original etcd-storm root cause (slow N5095 + mSATA fsync) removed.
- ✅ Phase 4b — Göndul Verd → Urd migration. Applied 2026-05-22. Göndul VM destroyed on Verd and recreated on Urd via `terraform apply --target='proxmox_virtual_environment_vm.control_plane["gondul"]'`; stale etcd member cleared via `kubectl delete node gondul`; rejoined as `--server` via `-e 'k3s_init_node=hlokk'` override. Surfaced orphan-LV class (NUC7-era partial migration). Vault Raft stayed 3/3 voters throughout. CP topology now: Göndul on Urd, Hlökk on Verd, Sigrún on Skuld — original 2026-05-14 design intent realised post-hardware-refresh.
- ✅ Worker rebuild — einherjar-urd template_node correction. Applied 2026-05-22 evening, immediately after Phase 4b. Corrected stale TF `template` / `template_node` references (pointed at Verd template even though VM ran on Urd). Doubled as deliberate worker-rebuild path validation; surfaced three findings (Vault chart's hard-required pod anti-affinity blocks cordon+migrate, iproute 6.17 `/etc/iproute2/rt_tables` not shipped, orphan-LV class). Vault accepted 2/3 voters during the ~25 min window by design. Procedure documented as Appendix C in `docs/teardown-rebuild.md`.
- ✅ Phase 5e.1 — Traefik + Gateway API + cert-manager — Deployed 2026-05-22 evening. Gateway API v1.5.1 Standard CRDs (vendored), cert-manager v1.19.0 (`enableGatewayAPI: true`), Traefik v40.2.0 (chart) / v3.7.1 (proxy) with Gateway API provider, MetalLB pool extended to `.10–.99`. Wildcard cert `*.niflheim.xiiisins.com` issued by Let's Encrypt prod (ECDSA P-256, E8 intermediate), DNS-01 via zone-scoped Cloudflare token. Authentik exposed at `https://authentik.niflheim.xiiisins.com` via HTTPRoute; original LB on `.12` released back to MetalLB pool. **Cluster edge stack now exists.** Surfaced eight findings — see incident log entry. **HelmRelease `install.remediation.retries: -1` left in place for cert-manager + Traefik; restore to `3` as part of post-flight (pending task).** Note: Authentik's `authentik.niflheim.xiiisins.com` FQDN is wrong-zone for a service that's about to be publicly reachable (`niflheim` is internal-only) — migrates to `authentik.xiiisins.com` (external) + `authentik.midgard.xiiisins.com` (internal alias) in Phase 5e.2.
- 🟡 Phase 5e.2 — Cloudflared + apex zone + WebFinger — Deployed 2026-05-23. Cloudflared 2026.5.0 (3× replicas, hostname anti-affinity, locally-managed tunnel `asgard-k3s`) in `infrastructure/cloudflared/` with `credentials.json` via ESO from Vault (`secret/k8s/cloudflared/credentials`, written by `terraform/cloudflare/`). Two new wildcards `*.midgard.xiiisins.com` + `*.xiiisins.com` (apex SAN includes bare apex) via cert-manager DNS-01. Second `midgard` Gateway in Traefik with three HTTPS listeners (`websecure-midgard` `*.midgard.xiiisins.com`, `websecure-apex-wildcard` `*.xiiisins.com`, `websecure-apex-bare` `xiiisins.com`). WebFinger served by Caddy 2.11.2-alpine pod (`apps/apex-static/`, 2 replicas) attached to the bare-apex listener — RFC 7033-compliant `application/jrd+json` response. **Authentik HTTPRoute migration to midgard Gateway** initially missed at the 5e.2.f close (surfaced 12h later by Terraform reachability check at start of 5e.3.b — see incident log) — corrected 2026-05-23 after the fact. Authentik now reachable at `authentik.xiiisins.com` (external via tunnel) + `authentik.midgard.xiiisins.com` (internal alias via AdGuard rewrite to `10.0.20.10`). Apps tree wired (`k8s/asgard/flux-system/apps.yaml` depends on infrastructure). End-to-end validated.
- ✅ Phase 5e.3 — Tailscale OIDC blueprints + LXCs — Closed 2026-05-23. Sub-phases: **5e.3.a** ✅ (terraform/authentik/ bootstrap, provider 2026.2.0 pinned, smoke-test data lookup), **5e.3.b** ✅ (OAuth2Provider + Application + group binding + identity-as-data via `users.yaml`/`groups.yaml`; ghost migrated from blueprint to Terraform via `terraform import`; tailscale-users + authentik-admins + ssh-users groups; Vault writes per Module-ownership rule), **5e.3.c** ✅ (Tailscale OIDC smoke test — fresh tailnet created via `login.tailscale.com/start/oidc`, WebFinger discovery worked after disabling Cloudflare bot protections, OIDC + token exchange validated end-to-end; ghost logged in via Authentik), **5e.3.d** ✅ (terraform/tailscale/ ACL module — provider `tailscale/tailscale 0.28.0` pinned, OAuth client tagged `tag:terraform-tag-owner` with scopes Policy File / Devices Core / Devices Routes / Auth Keys all Write, policy as external `policy.hujson` file using grants syntax with default-allow + autoApprovers `10.0.0.0/16` supernet for tag:subnet-router + 0.0.0.0/0+::/0 for tag:exit-node; bootstrap pattern manual tagOwners-in-UI → OAuth client → `terraform import tailscale_acl.this acl` → TF takes over; Tailscale Trial 14 days remaining then downgrades to Free — custom-OIDC + Trust credentials + OAuth all on Free, no impact expected), **5e.3.e** ✅ (LXCs 1113/1114/1115 = Bifrost/Heimdall/Gjallarbru — Terraform LXCs with `device_passthrough` for `/dev/net/tun`, `tailscale_tailnet_key` per host with reusable + recreate_if_invalid auto-renewing pattern, authkeys at `secret/ansible/tailscale/authkeys/<hostname>` per consumer-domain Vault convention, new `ansible/roles/tailscale/` role with apt-repo + install + sysctl forwarding + tailscale up, played to all three LXCs, all joined tailnet, Bifrost+Heimdall route `10.0.0.0/16`, Gjallarbru exit node — no warnings), **5e.3.f** ✅ (Munin DSM Tailscale subnet-router via Synology Package Center; DSM-7 TUN boot task running `tailscale configure-host`; UI-minted authkey stored in 1Password — first homelab secret following the human-consumer → 1P rule with no Vault path; `policy.hujson` extended to add `autogroup:admin` to `tagOwners["tag:subnet-router"]` so admin-UI key minting works). Tailscale Free plan (3-user cap); split-auth: LXCs 1113/1114/1115 + one always-on user device on auto-renewing TF-minted keys, NAS authkey out-of-band (UI-minted, 1P-stored), phones/laptops with default expiry through Authentik OIDC.
- 🔲 Remaining asgard LXCs (Tailscale, Teamspeak, PostgreSQL cluster expansion, HAProxy, Zabbix, Jellyfin)
- 🔲 Jotunheim K3s
- 🔲 Services

---

## Repo structure

```
homelab/
├── CLAUDE.md
├── renovate.json
├── terraform/
│   ├── proxmox/
│   │   ├── asgard-k3s/      # VM definitions (bpg/proxmox provider) — CP cpu/memory per-node via locals map
│   │   └── asgard-lxcs/     # LXC definitions — 1120 Factorio, 1130 Fulla (PG primary); cluster nodes for_each via locals.postgres_nodes
│   ├── vault/               # Vault config — KV engine, K8s auth, eso policy + role, AppRole + 2 roles, test KV entry
│   ├── cloudflare/          # NEW 5e.2 — tunnel object + DNS records + Vault KV write for cloudflared credentials.json (cloudflare/cloudflare 5.19.0, hashicorp/vault 4.8.0, hashicorp/random 3.6.3)
│   ├── authentik/           # NEW 5e.3 — Authentik config (users + groups + OAuth2Providers + Applications + PolicyBindings) via goauthentik/authentik 2026.2.0. Identity-as-data: users.yaml + groups.yaml. Tailscale OIDC provider+app + group gate in tailscale.tf. Generated client secrets to Vault per module-ownership rule.
│   ├── tailscale/           # NEW 5e.3.d — Tailnet ACL policy file via tailscale/tailscale 0.28.0. OAuth client (Trust credentials in Tailscale UI, scopes Policy File/Devices Core/Devices Routes/Auth Keys Write, tagged tag:terraform-tag-owner) credentials read from Vault (`secret/k8s/tailscale/oauth-client`). Policy as external `policy.hujson` file using grants syntax (Tailscale's current recommended syntax, replaces legacy `acls`). Default-allow within tailnet; autoApprovers route 10.0.0.0/16 supernet via tag:subnet-router. Tailscale Auth Keys for LXCs (5e.3.e) + Munin (5e.3.f) will be minted via `tailscale_tailnet_key` resources in this module.
│   ├── dns/                 # scaffolding (empty)
│   ├── aws/                 # scaffolding (empty)
│   ├── k3s/                 # scaffolding (empty)
├── ansible/
│   ├── ansible.cfg
│   ├── inventory/
│   │   ├── hosts.yml        # groups: asgard_k3s_cp, asgard_k3s_workers, factorio_host, postgres_hosts
│   │   ├── group_vars/all/  # vars.yml + vault.yml (Ansible Vault encrypted) — adjacent to inventory for auto-discovery
│   │   └── group_vars/{factorio_host,postgres_hosts}.yml  # per-group service config
│   ├── playbooks/
│   │   ├── asgard-k3s.yml         # roles: baseline → k3s → hardening
│   │   ├── factorio-host.yml      # roles: baseline → factorio → sftpgo → hardening
│   │   ├── postgres-host.yml      # roles: baseline → postgres → hardening
│   │   └── test-vault-lookup.yml  # smoke test for AppRole + Vault KV chain
│   └── roles/
│       ├── baseline/        # OS prereqs (sudo, acl, tzdata, gnupg, ca-certificates), timezone, locale, pkg update, ansible + recovery users, /etc/resolv.conf (cloud-init's DNS management disabled)
│       ├── postgres/        # PG 17 from PGDG, TLS, scram-sha-256, management users (admin, ansible), per-service DB provisioning
│       ├── k3s/             # prereqs, network.yml (sysctls + VLAN 20 policy routing), detect-state.yml (skip-install on healthy), config.yml (templates run always — was bundled in install.yml until 2026-05-21), install.yml, calico.yml
│       └── hardening/       # SELinux, SSH config, sysctl (security only — K3s+MetalLB sysctls are in k3s role), module blocklist, banner
├── k8s/
│   ├── asgard/
│   │   ├── flux-system/
│   │   │   ├── flux-system/                  # Flux bootstrap manifests
│   │   │   ├── infrastructure.yaml           # Flux Kustomization → infrastructure/
│   │   │   ├── apps.yaml                     # Flux Kustomization → apps/, dependsOn infrastructure (NEW 5e.2.f)
│   │   │   ├── infrastructure-config.yaml    # Flux Kustomization → infrastructure-config/ (ESO config), dependsOn infrastructure
│   │   │   ├── metallb-config.yaml           # Flux Kustomization → metallb-config/, dependsOn infrastructure
│   │   │   ├── vault-config.yaml             # Flux Kustomization → vault-config/ (vault-unseal SealedSecret), dependsOn infrastructure
│   │   │   ├── synology-csi-config.yaml      # Flux Kustomization → synology-csi-config/ (synology-csi SealedSecret), dependsOn infrastructure
│   │   │   ├── cert-manager-config.yaml      # Flux Kustomization → cert-manager-config/ (Cloudflare ExtSecret + ClusterIssuers), dependsOn infrastructure
│   │   │   └── gateway-config.yaml           # Flux Kustomization → gateway-config/ (wildcard Certificates + Gateways), dependsOn infrastructure + cert-manager-config
│   │   ├── infrastructure/
│   │   │   ├── sealed-secrets/              # helmrelease + namespace + kustomization
│   │   │   ├── synology-csi/                # helmrelease + namespace + kustomization (SealedSecret in synology-csi-config/)
│   │   │   ├── vault/                       # helmrelease + namespace + kustomization (SealedSecret in vault-config/)
│   │   │   ├── external-secrets/            # helmrelease + namespace + kustomization
│   │   │   ├── metallb/                     # helmrelease + namespace + kustomization
│   │   │   ├── gateway-api/                 # vendored upstream CRDs v1.5.1 + kustomization (Standard channel)
│   │   │   ├── cert-manager/                # helmrelease v1.19.0 + namespace + kustomization (enableGatewayAPI: true)
│   │   │   ├── traefik/                     # helmrelease v40.2.0 + namespace + kustomization (Gateway API provider only, NET_BIND_SERVICE for 80/443)
│   │   │   ├── authentik/                   # helmrelease + redis + externalsecret + blueprints (00-brand, 01-users) + httproute + namespace + kustomization
│   │   │   ├── cloudflared/                 # NEW 5e.2.e — namespace + externalsecret + configmap (config.yaml) + deployment (3 replicas, anti-affinity, no autoscaling) + kustomization
│   │   │   └── kustomization.yaml           # parent — references sub-kustomization dirs only (sub-kustomization-per-component pattern, settled 2026-05-17 alongside Authentik)
│   │   ├── infrastructure-config/
│   │   │   ├── clustersecretstore.yaml       # ESO ClusterSecretStore
│   │   │   └── kustomization.yaml
│   │   ├── metallb-config/
│   │   │   ├── metallb-config.yaml           # IPAddressPool .10–.99 + L2Advertisement (extended .11 → .10 for Traefik 5e.1)
│   │   │   └── kustomization.yaml
│   │   ├── vault-config/
│   │   │   ├── vault-unseal-secret.yaml      # SealedSecret with AWS KMS creds
│   │   │   └── kustomization.yaml
│   │   ├── synology-csi-config/
│   │   │   ├── synology-secret.yaml          # SealedSecret with Synology API creds
│   │   │   └── kustomization.yaml
│   │   ├── cert-manager-config/             # NEW 5e.1 — depends on infrastructure
│   │   │   ├── externalsecret-cloudflare.yaml      # Cloudflare API token from Vault → K8s Secret
│   │   │   ├── clusterissuer-letsencrypt-staging.yaml
│   │   │   ├── clusterissuer-letsencrypt-prod.yaml
│   │   │   └── kustomization.yaml
│   │   ├── gateway-config/                  # 5e.1 + extended 5e.2 — depends on infrastructure + cert-manager-config
│   │   │   ├── certificate-wildcard-niflheim.yaml  # *.niflheim.xiiisins.com via DNS-01 (renamed from certificate-wildcard.yaml in 5e.2.b)
│   │   │   ├── certificate-wildcard-midgard.yaml   # *.midgard.xiiisins.com (NEW 5e.2.b)
│   │   │   ├── certificate-wildcard-apex.yaml      # apex + *.xiiisins.com (NEW 5e.2.b)
│   │   │   ├── gateway-niflheim.yaml        # Gateway with web + websecure listeners
│   │   │   ├── gateway-midgard.yaml         # NEW 5e.2.c — Gateway with 3 HTTPS listeners (no HTTP — niflheim's :80 covers it)
│   │   │   └── kustomization.yaml
│   │   └── apps/                            # NEW 5e.2.f — wired via flux-system/apps.yaml
│   │       ├── apex-static/                 # Caddy 2.11.2-alpine pod serving /.well-known/webfinger on bare apex (HTTPRoute attaches to midgard Gateway websecure-apex-bare listener)
│   │       │   ├── namespace.yaml
│   │       │   ├── configmap.yaml           # Caddyfile + webfinger.json
│   │       │   ├── deployment.yaml          # 2 replicas, anti-affinity
│   │       │   ├── service.yaml
│   │       │   ├── httproute.yaml           # cross-ns to traefik/midgard
│   │       │   └── kustomization.yaml
│   │       └── kustomization.yaml           # aggregator
│   └── jotunheim/
│       ├── flux-system/
│       ├── infrastructure/
│       └── apps/
└── docs/
    ├── homelab-design.md
    └── teardown-rebuild.md                   # rebuild runbook
```

---

## K3s install — how it actually works

K3s itself is fully IaC'd via the Ansible `k3s` role. It is NOT a source of config drift — the drift concern is the in-cluster *workload* layer and the Calico addon merge behavior, not the K3s install.

- **Version:** `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.33.1+k3s1`), binary downloaded from GitHub releases.
- **Role task order:** `prerequisites.yml` → `network.yml` (sysctls + VLAN 20 policy routing) → `detect-state.yml` (skip-install gate) → `config.yml` (template render — runs always, notifies restart-k3s on change) → `install.yml` (skip when healthy) → `calico.yml` (init node only, skip when healthy).
- **`config.yml` runs always, not just on fresh installs.** Until 2026-05-21 the config-template tasks lived inside `install.yml`, which was wholesale-skipped on healthy nodes. Genuine config changes (taint adjustments, sysctls, etc.) never rendered. Split out 2026-05-21 during Phase 4a. The handler-fires-on-healthy-CP concern (potential "duplicate node name" on restart) was empirically *not* observed when restart-k3s fired during the Phase 4a deploy — see Known gotchas, downgraded from "rule" to "caveat".
- **Idempotency:** `detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s` returns `active` AND the node is `Ready` in the cluster. When true, `install.yml` and `calico.yml` are skipped. Re-running the playbook against a healthy cluster produces `changed=0` for K3s tasks — critical to avoid duplicate-join failures.
- **Bootstrap order:** `k3s_init_node` (default `gondul`) initialises with `--cluster-init` → other CP nodes join → workers join last. Enforced by `when` conditions + `wait_for` on :6443 + node-count gates.
- **CP rebuild scenario:** if you destroy and recreate the default init node, override with `-e k3s_init_node=hlokk` (or another healthy CP) so the rebuilt node *joins* the existing cluster instead of `--cluster-init`ing a new one. Also run `kubectl delete node <name>` first to remove the stale member entry (see Known gotchas).
- **Config templates:** `config-init.yaml.j2` (first CP), `config-server.yaml.j2` (joining CPs), `config-agent.yaml.j2` (workers). CP configs disable `traefik`, `servicelb`, `local-storage`; set `flannel-backend: none` + `disable-network-policy: true` (Calico handles both); set `cluster-cidr`/`service-cidr`, TLS SANs, `selinux: true`. Worker config is minimal: `server` + `token` + `selinux: true`.
- **Token:** generated by K3s on the init node, slurped and distributed as the `k3s_token` fact. (`k3s_token` default in defaults/main.yml is empty — real value in `group_vars/all/vault.yml`.)
- **kubeconfig:** fetched to `~/.kube/niflheim-asgard.yaml`, server address rewritten from `127.0.0.1` to the init node IP.
- **Prerequisites** (`prerequisites.yml`): Rancher k3s-selinux repo, `iscsi-initiator-utils` + `iscsid` enabled, `br_netfilter` + `overlay` modules loaded & persisted, `ip_forward=1`, `bridge-nf-call-iptables`/`ip6tables=1`, swap disabled, `firewalld` disabled.
- **Network plumbing** (`network.yml`): sysctls `rp_filter=2` (loose), `route_localnet=1`, plus the `vlan20-policy-routing.service` systemd unit on workers. See MetalLB section for why.
- **Node taints:** `node-role.kubernetes.io/control-plane=:NoSchedule` applied 2026-05-21 (Phase 4a). The `node-taint:` key in `config-init.j2` + `config-server.j2` registers the taint at *new* node bootstrap only — K3s does NOT re-apply node-taint config to existing nodes on restart. For existing CPs, the taint is set via `kubectl taint node <cp> node-role.kubernetes.io/control-plane=:NoSchedule`. Going forward both paths are in place: config-template covers fresh bootstraps (Phase 4b's gondul rebuild, future cluster rebuilds), kubectl covers the current cluster. No labels set anywhere.

---

## K3s VM specs

CP cpu/memory parameterized per-node in the `locals.control_planes` map in `terraform/proxmox/asgard-k3s/main.tf`.

**Control planes (Göndul/Hlökk/Sigrún):**
- Göndul: 2 vCPU, 4GB RAM, 10GB disk, on **Urd** (Phase 4b applied 2026-05-22 — back to original 2026-05-14 design intent post-hardware-refresh)
- Hlökk: 2 vCPU, 4GB RAM, 10GB disk, on Verd
- Sigrún: 2 vCPU, 4GB RAM, 10GB disk, on Skuld
- Single NIC on VLAN 21
- RHEL 9, K3s, Calico CNI
- Bumped from 1vCPU/2GB → 2vCPU/4GB on 2026-05-17 during Authentik deploy after hlokk OOM'd / went into I/O thrash under the migration+blueprint reconciliation burst. CP sizing is symmetric across nodes — failover symmetry requires it.
- **Phase 4a applied 2026-05-21:** All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Workload pods cannot land on them. K3s-shipped components (CoreDNS, metrics-server, calico-apiserver) and DaemonSets that tolerate the taint (Calico-node, kube-proxy, metallb-speaker) remain on CPs. Synology CSI node-plugin DaemonSet was evicted as expected — see Known gotchas for the caveat about that. 4 GiB CP sizing is sufficient with this posture.
- **Phase 4b applied 2026-05-22:** Göndul moved from Verd back to Urd. Realises the post-hardware-refresh intent. Procedure was clean (Appendix B in `docs/teardown-rebuild.md`); one finding (orphan LVs from a NUC7-era partial migration blocked the first clone — see Known gotchas).

**Workers (Einherjar-urd/verd/skuld):**
- 2 vCPU, 4GB RAM, 15GB disk
- eth0: VLAN 21 (K3s traffic)
- eth1: VLAN 20 (MetalLB L2, IPs 10.0.20.201/202/203)
- RHEL 9, K3s, Calico CNI, iscsiadm
- ⚠️ Multi-homed. The second NIC (eth1/VLAN 20) is a known landmine: it requires Calico autodetection pin, rp_filter loose mode, route_localnet=1, AND VLAN 20 source-based policy routing. All four are in IaC. See Known gotchas + MetalLB decision.

---

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- Versions: All IaC versions are concrete-pinned — Helm charts (since 2026-05-22), Ansible role versions (`k3s_version`, `calico_version`), and Terraform providers (rule generalized 2026-05-23, applied to new `terraform/cloudflare/` from day 1; existing `terraform/vault/` `~> 4.0` is a known pending tighten). The earlier "0.x placeholders, pin at 2.0" position was retired after the 2026-05-21 Phase 4a session — two outages from floating pins in one day (metallb 0.16 unconditional `prometheus.serviceMonitor` reference; synology-csi 0.11.2 bumping appVersion to 1.3.0 with that image not published on Docker Hub) disproved the assumption that minor-version bumps would be safe. Updates are deliberate operations — bump the pin, reconcile, verify. Renovate remains deferred until the homelab reaches stable state.
- Conventional commits
- Norse mythology naming
- **File-path header** — every source file in the repo starts with a comment line containing its repo-relative path (e.g. `# k8s/asgard/apps/apex-static/configmap.yaml`). Lets `grep` on a known path-string find moved files even after a `git mv`. Applies to all `.tf`, `.yaml`, `.yml`, `.j2`, `.sh`, `.fish`, `.py` files. Existing pre-convention files: retroactive sweep pending (see Pending tasks in `docs/homelab-design.md`).
- Never commit secrets
- **Terraform state is never committed.** `*.tfstate`, `*.tfstate.backup`, and `.terraform/` are gitignored across every `terraform/*/` module. State contains decrypted secrets (any `random_password`, any `vault_kv_secret_v2` `data_json`, etc.), generated credentials, and resource IDs that count as identity attestation — committing it would invert every Vault/1Password discipline in this repo. State lives on the operator workstation. Backup is the operator's responsibility (1Password attachment, encrypted external drive); long-term move to a Vault KV-backed remote backend is on the roadmap.
- Shell: fish (owner uses fish shell — heredocs `<<EOF` don't work, use pipes or temp files)

---

## Known gotchas

- **etcd on Urd — original incident, root cause removed 2026-05-21**: The 2026-05-14 etcd IO storm originated on Göndul running on the old Urd (N5095 + mSATA). Root cause was slow CPU + slow disk under fsync load. **The MSI Cubi hardware refresh on 2026-05-21 removes both factors** — Urd now has an i3-1215u + Lexar NM790 NVMe. The "never run CP on Urd" rule from that incident is retired. The structural lessons (per-component config Kustomizations, CRD timing, idempotency) remain. Phase 4b — Göndul Verd → Urd migration — is the planned realisation of this, deferred to a separate session.
- **Multi-homed workers — Calico autodetection**: Workers have eth0 (VLAN 21) and eth1 (VLAN 20). Calico's default `firstFound` autodetection bound the overlay to eth1 on the workers, breaking cross-node vxlan. Fix (in the Ansible Calico template): `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]`. Do NOT revert to firstFound.
- **Multi-homed workers — rp_filter**: Strict reverse-path filtering (`rp_filter=1`) silently drops MetalLB LoadBalancer traffic arriving on eth1. K3s role's `network.yml` sets `rp_filter=2` (loose mode) on `all` and `default` — required on these multi-homed nodes, still drops genuinely unroutable sources. Do NOT "harden" it back to 1. (Per-interface `eth0`/`eth1` values derive from `all`; setting them explicitly would be belt-and-suspenders.)
- **Multi-homed workers — route_localnet**: MetalLB L2 mode does NOT bind VIPs to any interface — it only ARPs for them. Without `route_localnet=1`, the kernel drops packets destined for the VIP because the IP isn't local. K3s role's `network.yml` sets it on `all`. Discovered 2026-05-17 during asgard rebuild — VIP was completely unreachable from outside the VLAN until this landed.
- **Multi-homed workers — VLAN 20 policy routing**: Replies from MetalLB VIPs originate from the worker's eth1 IP. Without source-based policy routing, the reply goes out via the default route (eth0/VLAN 21), creating asymmetric routing that UCG-Ultra's stateful firewall drops. Fix: the `vlan20-policy-routing.service` systemd oneshot unit installed by `roles/k3s/tasks/network.yml` adds `from 10.0.20.0/24 lookup vlan20` + `default via 10.0.20.1 dev eth1 table vlan20`. OS-independent (pure ip(8) + systemd, no NetworkManager coupling).
- **MetalLB L2 election**: L2Advertisement needs `nodeSelectors` excluding CP nodes (they have no eth1). Without it, election can land on a CP node and announce nowhere.
- **Calico Installation CR merge behavior**: The K3s addon controller MERGES the `Installation` CR rather than replacing it — a removed field can persist in the live object (observed 2026-05-14: a stale `firstFound` survived alongside the new `cidrs`). After changing autodetection, verify with `kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}'` and `kubectl patch ... --type=json` to strip stale fields if needed.
- **tigera-operator `/var/lib/calico/mtu` denied (upstream bug #7851)**: The Tigera operator runs as `container_t` with MCS categories (e.g. `s0:c322,c902`); `/var/lib/calico/mtu` is written by privileged calico-node as `container_var_lib_t:s0` with NO categories. MCS dominance fails on read. The denial is `dontaudit`'d by default policy, so `ausearch` returns nothing until `semodule -DB`. **Fix (shipped 2026-05-15):** set `mtu: 1450` explicitly in the Calico `Installation` CR (`ansible/roles/k3s/templates/calico-installation.yaml.j2`). When MTU is set explicitly the operator never reads the file. This is the upstream-maintainer-recommended workaround. 1450 = 1500 (host) - 50 (VXLAN overhead). Do NOT remove without a replacement fix — the operator will go Degraded again. Revisit if/when upstream actually fixes the operator.
- **Vault SKIP_CHOWN**: Vault Helm chart sets SKIP_CHOWN=true when running as non-root. With iSCSI storage, fsGroup doesn't apply automatically. Fix (in the Vault HelmRelease values): pod-level `securityContext` with `runAsUser: 100`, `runAsGroup: 1000`, `fsGroup: 1000`, `runAsNonRoot: false`; plus an `extraInitContainer` `vault-data-chown` (busybox, `runAsUser: 0`) running `chown -R 100:1000 /vault/data && chmod 750 /vault/data`.
- **Vault TLS disabled — deliberate**: The Vault raft config sets `tls_disable = 1` on the listener; there is no TLS on the listener OR cluster traffic (`cluster_address` is under the same listener). This is a conscious homelab tradeoff (simplicity vs defense-in-depth), not an oversight. Do NOT "fix" it without understanding the cluster-traffic implications. Revisit only as part of deliberate Vault hardening. The `vault-ui` LoadBalancer (VIP `10.0.20.11`) therefore serves plaintext HTTP on :8200 — it is internal/VLAN-only.
- **iSCSI single-session LUNs**: Synology CSI LUNs are single-session. After ungraceful node restarts, a stale session or discovery node record can pin a LUN to a dead/wrong node and block re-attach (`non-retryable iSCSI login failure`, or `iscsi_limit_max_session_count` on the NAS side). Cleanup: `iscsiadm -m session` / `-m node -o delete` on affected nodes; clear stale sessions NAS-side if needed. **Workload concentration:** during the 2026-05-17 Authentik deploy, all 5 stateful workload PVCs ended up colocated on `einherjar-skuld` due to LUN pinning across rebuilds. Load 1.84 vs 0.34 on peers. Not a hot bug but a structural pattern — StatefulSet pod spread doesn't override CSI's pinning once a LUN has been seen on a particular node. **Mitigation (Phase 4a, applied 2026-05-21):** CP taint evicted the Synology CSI node-plugin from CPs as intended, closing the CP-grabs-worker-LUN failure mode. **But surfaced a new failure mode (see "CSI eviction footgun"):** stateful pods that were already on a CP at taint-time can't unmount cleanly, because the CSI driver is gone. Net architectural posture: CSI off CPs *is* what we want for the cross-node iSCSI fight gotcha, *and* we need to drain stateful workloads from CPs before tainting (or add a CSI toleration — see open questions). Pod anti-affinity for stateful workloads remains a separate concern for the worker-concentration class. Post-Phase 4a, Vault HA is naturally spread (vault-0 on einherjar-urd, vault-1 on einherjar-skuld, vault-2 on einherjar-verd).
- **Flux CRD timing — SealedSecrets**: SealedSecret resources can NOT live in the same Kustomization that installs the sealed-secrets HelmRelease. The dry-run runs before the chart installs → SealedSecret CRD doesn't exist yet → reconcile fails with `no matches for kind "SealedSecret" in version "bitnami.com/v1alpha1"`. Same rule applies to any CRD-dependent resource: it goes in a per-component `<component>-config/` Kustomization that `dependsOn: infrastructure`. Current `<component>-config` Kustomizations: `infrastructure-config` (ESO ClusterSecretStore), `metallb-config` (IPAddressPool + L2Advertisement), `vault-config` (vault-unseal SealedSecret), `synology-csi-config` (synology-csi SealedSecret).
- **Sealed-secrets master keys must be backed up**: sealed-secrets controller generates a fresh keypair on first start. SealedSecrets in Git are encrypted against that pair's public cert. If the cluster is rebuilt without restoring the keypair, every existing SealedSecret becomes undecryptable and must be re-sealed from plaintext sources. Backup procedure: `kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml > backup.yaml`, store contents in 1Password. Restore: `kubectl apply -f backup.yaml` *before* the sealed-secrets controller starts on the rebuilt cluster. Discovered 2026-05-17 — both SealedSecrets (vault-unseal, synology-csi) had to be re-sealed from plaintext during the asgard rebuild because the original keys were never backed up.
- **CP rebuild → "duplicate node name"**: destroying and recreating a CP VM and re-running the playbook fails with `etcd cluster join failed: duplicate node name found`. K3s tries to join with the same name the cluster already considers a member. Fix: `kubectl delete node <name>` from a surviving CP *before* starting K3s on the new VM. The K3s native node-delete handler also evicts the stale etcd member. If the failing k3s.service is already in a systemd restart loop, the next retry will succeed automatically once the node entry is removed.
- **CP rebuild of the default init node**: if the destroyed CP is `k3s_init_node` (default `gondul`), the role would `--cluster-init` it as a fresh cluster instead of joining the existing one. Override: `ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'` (any healthy CP works as the temporary init reference). The new node then joins via that CP.
- **K3s role install-skip on healthy nodes**: `roles/k3s/tasks/detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s == 'active'` AND `kubectl get node <name>` returns Ready. `install.yml` and `calico.yml` skip when true. **Until 2026-05-21**, config-template tasks lived inside `install.yml` and were also wholesale-skipped — meaning config changes (taint adjustments, sysctl tweaks) never rendered on healthy nodes. **Fixed:** config templating moved to its own `config.yml` task file, which runs always. When config changes, the restart-k3s handler fires and K3s restarts cleanly. Empirically during the Phase 4a deploy this did NOT trigger the "duplicate node name found" failure that the older doc text warned about — the steady-state-restart case is safer than the fresh-cluster-bootstrap case. The handler-safety task in homelab-design.md open questions stays as future-proofing.
- **Vault Raft follower auto-join can fail**: on fresh Vault init via the Helm chart's StatefulSet, vault-1 and vault-2 are supposed to auto-discover vault-0 and join via `retry_join`. They often miss the join window if they started before vault-0 was initialized, then sit logging "stored unseal keys are supported, but none were found." Fix: `kubectl exec -n vault vault-<N> -- vault operator raft join http://vault-0.vault-internal:8200`. They auto-unseal via KMS after joining.
- **Vault init can leave a stuck partial state**: if `vault operator init` exits non-zero (e.g. running it before all pods are stable), the Raft data dir can end up in a half-initialized state where the next init attempt also fails with "stored unseal keys supported, but none were found." Recovery: `kubectl delete statefulset vault -n vault --cascade=orphan`, `kubectl delete pvc -n vault data-vault-0 data-vault-1 data-vault-2`, `kubectl delete pod -n vault vault-0 vault-1 vault-2 --force --grace-period=0`, then `flux reconcile helmrelease vault -n vault --force` to recreate. Old iSCSI LUNs on Synology retain-policy survive — clean them up on DSM after.
- **Flux deploy key not in IaC**: re-bootstrapping Flux on a fresh cluster needs the GitHub deploy key Secret. `flux bootstrap github` re-creates it idempotently if the deploy key still exists in the GitHub repo settings. If not, generates a new keypair. NOT a SealedSecret. Tracked: should be backed up or rotated post-bootstrap.
- **ESO API version**: ClusterSecretStore uses `external-secrets.io/v1` not `v1beta1`.
- **MetalLB speaker labels**: `app.kubernetes.io/component=speaker` not `component=speaker`.
- **fish shell heredocs**: Don't use `<<EOF` syntax. Use `echo '...' | command` or temp files.
- **Ansible vault loading**: `group_vars/all/` directory with `vars.yml` + `vault.yml`. `k3s_token` is defined in `vault.yml` (default in role is empty).
- **Rancher SELinux repo path**: `prerequisites.yml` pulls `k3s-selinux` from the Rancher `centos/8/noarch` repo even on RHEL 9 nodes. This is the documented Rancher path and the RPM is noarch — works fine, intentional, not a bug.
- **baseline role updates all packages**: The `baseline` role runs `dnf update "*" → latest` + reboot on every run. Re-running the playbook is therefore not pure config-idempotency — it can pull OS updates. Known/intended behavior.
- **bpg/proxmox reboots**: Provider may reboot VMs on apply even without meaningful changes due to IP address drift in state. Expect this — cluster should survive rolling restarts.
- **SELinux dontaudit hides denials**: A "permission denied" with nothing in `ausearch` does NOT mean SELinux is innocent. The default policy `dontaudit`s many denials. Diagnose with `semodule -DB` (disable dontaudit), reproduce, capture AVC, then `semodule -B` to re-enable. Never leave dontaudit disabled — it floods the audit log.
- **Proxmox minimal Debian template is *minimal***: `debian-13-standard_*.tar.zst` lacks `sudo`, `acl`, and other things roles often assume. The `baseline` role installs `sudo` and `acl` OS-agnostically (in `main.yml`, via `ansible.builtin.package` — no-op on RHEL which ships both in `@core`). When writing a new role that uses `become_user: <non-root>`, ensure `acl` is installed via baseline first (Ansible's preferred privilege-drop mechanism uses POSIX ACLs).
- **Minimal-template package gaps keep surfacing**: PG deploy 2026-05-17 added `tzdata`, `gnupg`, `ca-certificates`, and locale generation (`locales` package + `community.general.locale_gen`) to baseline. The list will grow as new roles surface new gaps — that's expected. Baseline owns the class; alternative is every role rediscovering the same gaps. Particularly: any role adding a third-party apt repo needs `gnupg` (for `apt-key`-style dearmor) and `ca-certificates` (for HTTPS to upstream). Any role using locale-sensitive tools (PG's `pg_createcluster` is one) needs the locale generated first.
- **`--check` doesn't validate file ownership**: `--check` reports "would create" for files that don't yet exist, but doesn't validate `owner`/`group`/`mode` against actual users. Bugs in this dimension survive `--check` and only surface at real-run + post-hoc inspection. The auth path is unforgiving — sshd silently rejects keys with wrong directory perms or ownership. **Always set `owner`/`group`/`mode` explicitly on every `file`/`template`/`copy` task; never rely on defaults**. Discovered 2026-05-17 when fulla's `authorized_keys` ended up owned by `recovery` instead of `ansible` and the fix took longer than the bug.
- **`ansible.posix.authorized_key` in `--check` needs explicit `path:`**: The module resolves `~/.ssh/authorized_keys` from the user's home dir via getent. In `--check`, the previous create-user task is a no-op, getent finds no user, module errors with "Either user must exist or you must provide full path to key file in check mode". Fix: pass `path: "/home/{{ user }}/.ssh/authorized_keys"` + `manage_dir: true` explicitly. Real-run is functionally identical; check-mode unblocks.
- **`ansible.builtin.user` creates accounts with `password: '!'` by default**: PAM's `account` stack interprets this as "account locked" even though pubkey auth via sshd never actually checks a password. Symptom after hardening enables full sshd PAM evaluation: `User <name> not allowed because account is locked` in auth.log; SSH key offered and rejected. Fix: pass `password: '*'` explicitly. `*` is "valid account, no usable password, key auth permitted." Required for any key-auth-only user.
- **Service-specific systemd drop-in dirs (`/etc/systemd/system/<unit>.service.d/`) don't pre-exist**: Roles that drop override.conf files must `file: state: directory` first. SFTPGo bit us; assume any service-specific override needs the dir created.
- **bpg/proxmox API tokens can change `nesting` but no other features**: `keyctl`, `fuse`, `device_passthrough`, and other LXC features require `root@pam` to change once the container exists — and `device_passthrough` ALSO requires `root@pam` to *create*, not just modify. Workaround: aliased provider pattern. Define a second `provider "proxmox" { alias = "root", username = "root@pam" }` block, set `PROXMOX_VE_PASSWORD` in the env (and declare `configuration_aliases = [proxmox.root]` in `versions.tf`), then add `provider = proxmox.root` on resources needing these features. Practical implications: (a) don't put `keyctl: false`/`fuse: false` in the resource block — they're defaults, omitting them avoids future "tried to change keyctl, 403" plans even when values aren't actually changing; (b) for `device_passthrough` (e.g. `/dev/net/tun` on the Tailscale LXCs), the aliased provider is mandatory at create-time, not just for in-place changes — discovered 2026-05-23 during 5e.3.e.iii when API-token apply failed on a fresh `device_passthrough` block.
- **Systemd 257 in unprivileged LXC requires `nesting=true`**: Debian 13 ships systemd 257; it uses namespace operations that need CAP_SYS_ADMIN inside the user namespace, which `nesting=true` provides. This flag does NOT enable nested containerization — its name is misleading. The "no nested containerization" rule is preserved by `unprivileged=true`, not by `nesting=false`.
- **`-u root` CLI flag does NOT override `ansible_user` from group_vars**: Group_vars are inventory-level data and outrank CLI `-u` in Ansible's variable precedence. To override during bootstrap, use `-e 'ansible_user=root'` — `-e` is the only level above inventory. Same issue affects the `asgard-k3s` flow if you ever need to re-bootstrap.
- **`group_vars/` is auto-discovered only adjacent to inventory or playbook directories**: `ansible/group_vars/` is invisible to Ansible; must be `ansible/inventory/group_vars/`. If a play seems to be ignoring vault variables, this is usually why.
- **SFTPGo sqlite `data_provider_name` must be an absolute path**: The Debian package unit sets `WorkingDirectory=/etc/sftpgo`, so a relative `sftpgo.db` lands in `/etc/sftpgo/sftpgo.db` — works but FHS-wrong (config dir holding state). Set explicitly to `/var/lib/sftpgo/sftpgo.db` and ensure that dir exists with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in the role**: Background timer firing reconcile before `initial-install.yml` runs causes a race: factorio installs and the service starts (control file defaults to `state=running`) before Ansible can generate the default save → `factorio --create` fails on the .lock file held by the running service. Pattern: enable timer only in `reconcile.yml`, start it explicitly at the end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract**: Factorio writes `.lock` in its install dir at startup. The tarball's extracted ownership won't match. The reconcile script does this; if you ever sidestep reconcile and install manually, you need to chown manually too.
- **Public resolvers as DNS fallback poison internal-zone resolution**: Cloud-init left `nameserver 1.1.1.1` as secondary in `/etc/resolv.conf` on all K3s nodes. When primary (UCG → AdGuard) was briefly unreachable or slow, libresolv fell back to Cloudflare, which returns NXDOMAIN for `*.niflheim.xiiisins.com`. Glibc and CoreDNS treat NXDOMAIN as authoritative and cache it. CoreDNS then served stale NXDOMAINs to pods (Authentik couldn't reach `fulla.niflheim.xiiisins.com`). Symptom is hard to diagnose: same resolv.conf works in a freshly-run `kubectl run dnstest` busybox (different upstream timing), only the long-lived consumer hits the cached failure. Fix shipped in `roles/baseline/tasks/main.yml`: `manage_resolv_conf: false` cloud-init drop-in + template `/etc/resolv.conf` from `baseline_nameservers`. If a fallback resolver is wanted, point it at a peer AdGuard (`10.0.11.201/202/203`), never at a public resolver.
- **Synology CSI iSCSI volumes need a chown init container for non-root pods**: First documented for Vault (SKIP_CHOWN + iSCSI fsGroup quirk); now confirmed for Redis. Pattern: any stateful pod that needs non-root write access to its PVC needs a busybox `initContainer` running as UID 0 to `chown -R <uid>:<gid> /mountpath` before the main container starts. Synology CSI's iSCSI driver doesn't honor `securityContext.fsGroup` reliably regardless of K8s-level config. Currently shipped in: Vault (`vault-data-chown`), Authentik Redis (`redis-data-chown`).
- **Postgres `hostssl`-only rejects with "no pg_hba.conf entry"**: A client connecting in plaintext gets the exact same error as a client from a disallowed CIDR — `FATAL: no pg_hba.conf entry for host "X", user "Y", database "Z", no encryption`. The "no encryption" suffix is the giveaway. Always set explicit `sslmode=require` (or stricter) on PG clients connecting to Fulla — don't rely on libpq's default `prefer`, which silently downgrades on chart-injected connection strings.
- **Authentik chart values block does not override env vars (or vice versa, depending on key)**: When a setting is exposed both via `authentik.postgresql.host` in the chart values and `AUTHENTIK_POSTGRESQL__HOST` env var, the env var wins. Setting only the values block while leaving the env unset can produce silent localhost fallback. Going forward: set service config via ExternalSecret env vars, treat chart values blocks as default-documentation only. Same rule for `AUTHENTIK_POSTGRESQL__SSLMODE`, `__PORT`, `__USER`, `__NAME`, etc.
- **Authentik brand `default: true` is mutually exclusive cluster-wide**: Blueprint must first demote the shipped `authentik-default` brand (set `default: false` on it) before claiming default on your own. Single-entry blueprint fails with `Serializer errors {'default': [ErrorDetail(string='Only a single brand can be set as default.', ...)]}`. Both entries can live in the same blueprint file; blueprints apply in document order within a file.
- **Authentik blueprints — brand handles per-domain branding, tenants are for actual multi-tenant Postgres-schema isolation only**: Don't include `authentik_tenants.tenant` entries unless you genuinely want multi-tenant schema isolation. On a single-instance deploy, the brand model is the only object you need to express "this is my custom-branded login page." Including a `tenant` entry against `schema_name: public` fails because it conflicts with Authentik's implicit default tenant.
- **HelmRelease remediation retries can mask the actual install failure**: `install.remediation.retries: N` runs `helm uninstall` on each failure, then retries — so the *next* failure log no longer reflects the original problem, and ESO-managed Secrets with finalizers can leave uninstall stuck on Secret-termination timeout, compounding the noise. For first-deploy debugging set `retries: -1` (or remove the `install.remediation` block entirely) to inspect the failed state, then restore `retries: 3` after success. Also extend `spec.timeout` past the default 5min for charts with long first-deploy reconciles (Authentik's first migrations + blueprint loading wants ~15min).
- **CoreDNS forwards to `/etc/resolv.conf` from each replica's node**: CoreDNS's default Corefile uses `forward . /etc/resolv.conf`, which means each CoreDNS pod inherits the resolv.conf from the node it lands on. Two implications: (a) every node must have a working resolv.conf for upstream queries to work, (b) changes to node resolv.conf require `kubectl rollout restart deploy -n kube-system coredns` to pick up — CoreDNS reads resolv.conf at pod start, not per-query.
- **Kustomize `configMapGenerator` with `files: []` is invalid**: Generator entries with zero files fail the kustomize build. If the directory is empty initially (e.g. `branding/` for asset upload later), omit the generator entry entirely and add it in the follow-up commit when the first file lands. Don't keep an empty generator as a placeholder.
- **Kustomize-managed ConfigMaps that are referenced by name elsewhere need `disableNameSuffixHash: true`**: Default Kustomize behavior appends a content hash to ConfigMap names so updates trigger pod restarts. Charts and other manifests that reference the ConfigMap by static name (e.g. `blueprints.configMaps: [authentik-blueprints]` in the Authentik HelmRelease values) can't resolve the hashed name. Use `generatorOptions.disableNameSuffixHash: true` for these — updates mutate in-place; consumers pick them up on their own reconcile loop.
- **K3s `node-taint` config is registration-time only**: The `node-taint:` key in `/etc/rancher/k3s/config.yaml` is consulted by K3s only at *fresh* node registration. Restarting K3s on a node that's already a cluster member does NOT cause it to re-read and re-apply taints — the existing node object's `.spec.taints` is untouched. For an existing cluster, taints must be applied via `kubectl taint node <name> ...`. The config-template change still matters (covers future cluster rebuilds and Phase 4b's gondul re-registration), but it's not sufficient by itself. Discovered 2026-05-21 Phase 4a.
- **K3s restart on an existing CP member: empirically did NOT trigger "duplicate node name found"** during 2026-05-21 Phase 4a, contrary to the earlier doc claim that any restart of a healthy CP would cause it. The restart-k3s handler fired cleanly when `config.yml` was changed and the node rejoined the existing cluster via `--server` config (not `--cluster-init`). The "rule" was a precaution based on what *could* go wrong with fresh-cluster bootstrap; in steady-state restart it doesn't fire. Treat as a caveat to be aware of, not a hard rule blocking work. Restart-handler-safety task in homelab-design.md open questions remains as future-proofing, not blocker.
- **`NoSchedule` taint does NOT evict existing workload pods** — it only prevents *new* scheduling. The only kind of pod that's reconciled-against-taint-violation automatically is a DaemonSet (the DaemonSet controller respects taints when deciding which nodes to place pods on). Existing Deployment/StatefulSet/standalone pods on a newly-tainted node stay running indefinitely; they migrate only via natural churn (pod restart, node reboot) or explicit `kubectl delete pod`. Don't taint a CP that has stateful pods and expect them to move on their own.
- **CSI eviction from tainted CPs is a footgun, not a free side effect**: When a CP gets a NoSchedule taint and the CSI DaemonSet doesn't tolerate it, the CSI node-plugin pod on that CP is evicted. If a *stateful* workload pod was still on that CP, deleting it triggers an unmount path that *requires* the CSI driver socket — which is no longer there. The kubelet retries forever (`Unmounter.TearDownAt failed to get CSI client: driver name csi.san.synology.com not found in the list of registered CSI drivers`). The volume's `VolumeAttachment` object stays `ATTACHED=true NODE=<dead-CP>`, blocking re-attach elsewhere (`Multi-Attach error`). **Mitigation:** drain stateful workloads from a CP *before* applying the taint, OR add a CP-taint toleration to the CSI DaemonSet so it stays on CPs. Recovery from the stuck state: iscsiadm logout from the affected CP via debug pod, force-delete the pod, manually delete the stale VolumeAttachment object. Discovered 2026-05-21 during Phase 4a (vault-1 on gondul stuck Terminating for ~25 min until manually cleaned). Architectural decision pending — see homelab-design.md open questions.
- **Concrete version pinning required across all IaC, no floats** (rule established 2026-05-22 after two floating Helm-pin outages on 2026-05-21; generalized 2026-05-23 to cover all IaC pins — Helm charts, Terraform providers, Ansible role versions): Helm: (1) MetalLB chart `0.x` resolved to `0.16.0`, which adds an unconditional `.Values.prometheus.serviceMonitor.enabled` reference — no values block → template render fails. (2) synology-csi chart `0.x` resolved to `0.11.2`, which bumps appVersion to `v1.3.0` — and `synology/synology-csi:v1.3.0` is NOT published on Docker Hub (`NotFound`). All asgard HelmReleases now concrete-pinned (sealed-secrets 2.18.6, vault 0.32.0, external-secrets 0.20.4, metallb 0.15.3, synology-csi 0.11.1, authentik 2026.2.3). Minor-floats (`0.15.x`, `2.x`) are equally banned — they cover patch versions which proved breaking in the same session. Terraform providers: same rule (`~> 4.0` style is banned). The Cloudflare provider v4→v5 transition is exactly this failure mode one ecosystem over — 40+ resource renames in a minor-version stream. New `terraform/cloudflare/` pins concrete from day 1; existing `terraform/vault/` `~> 4.0` is a known pending tighten. Concrete pins only; updates are deliberate operations.
- **Failed Helm reconcile may need `flux suspend` + `helm rollback` + `flux resume`**: When a HelmRelease enters the `failed` state from a broken upgrade, plain `flux reconcile helmrelease` often retries the same broken upgrade and hangs. Recovery sequence: `flux suspend helmrelease -n <ns> <name>`, `helm rollback <name> <last-good-revision> -n <ns> --wait=false`, verify pods, `flux resume helmrelease -n <ns> <name>`, `flux reconcile helmrelease -n <ns> <name>`. If pods don't pick up the rollback automatically, `kubectl delete pod` on the affected ones to force template re-render. The HelmRelease must also have a corrected `spec.chart.spec.version` *before* resume or it'll fight the rollback. Discovered 2026-05-21.
- **CSI controller down → all VolumeAttachment operations stall cluster-wide**: The Synology CSI controller (single StatefulSet replica) is the orchestrator for all attach/detach operations. When it's CrashLoopBackOff, every pod that needs to attach a PVC (new pod, migrated pod, restarted pod) hangs at `Init:0/1` indefinitely. The kubelet-side node-plugins (DaemonSet) can be healthy and still nothing works. Single point of fragility; any controller-side outage is cluster-wide for storage. Worth knowing as the first diagnostic check when multiple unrelated pods are stuck on volume operations: `kubectl get pods -n synology-csi -o wide` and check controller status.
- **RHEL 9 e2fsprogs 1.46.5 cannot fsck Synology-CSI-formatted iSCSI LUNs**: The CSI driver formats with newer ext4 features (`orphan_file` introduced in e2fsprogs 1.47.0, plus ro-compat bit 16). `fsck.ext4` on RHEL 9 errors with `unsupported feature(s): FEATURE_C12 FEATURE_R16` and refuses. Workaround: fsck from an Alpine 3.20 debug pod. `kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh`, then `apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX`. RHEL 10 (when available) should ship a new-enough e2fsprogs; revisit then.
- **Released PVs with retain policy leave orphan iSCSI sessions** on the worker that last consumed them. The iSCSI session and discovery node record survive the PV's K8s lifecycle. They show up as `iscsiadm -m session` entries for `pvc-<uuid>` names whose PVs are in `Released` phase (or gone entirely). Doesn't break anything immediately, but consumes Synology's `iscsi_limit_max_session_count` quota and surfaces during cluster maintenance. Cleanup: `iscsiadm -m node -T <iqn> -p 10.0.254.20 --logout` then `-o delete`, plus NAS-side LUN cleanup via DSM if the underlying LUN is also abandoned. The asgard-health script's iSCSI section cross-references live sessions against `kubectl get pv` to flag orphans.
- **iSCSI session timeout → ext4 journal abort → FS RO**: A network blip during cluster maintenance (e.g. swapping a Proxmox host) can let the worker's iSCSI session timeout. Linux ext4 reacts by aborting the journal and remounting the FS read-only. The pod that had that PVC mounted is then stuck in a broken state until manual recovery. Recovery: scale workload to 0, attach the LUN to a workstation/debug pod, `fsck.ext4 -y /dev/sdX` (from Alpine — see RHEL 9 e2fsprogs gotcha above), detach, scale back up. Hit twice during 2026-05-21 Phase 4a (vault-0 + vault-1 PVCs after Urd hardware swap network blip).
- **Synology DSM-side stale state can block iSCSI re-attach**: If a VolumeAttachment is deleted but the Synology target still considers a previous initiator "connected" (DSM hasn't noticed the K8s-side cleanup yet), re-attach to a different node can fail with timeout-on-attach. Path through this: confirm DSM-side via SAN Manager → Target → Connected Initiators; manually disconnect stale initiator entries if needed. Hasn't been definitively confirmed as a single failure mode (the symptoms overlap with controller-side issues) — flagged here as a known possibility when standard recovery fails.
- **MetalLB chart 0.16+ requires explicit `prometheus` values block**: `0.16.0` introduced an unconditional `.Values.prometheus.serviceMonitor.enabled` reference in the speaker template. Template render fails without the block. Pinning to `0.15.x` avoids this; alternative is to define `prometheus.serviceMonitor.enabled: false` in HelmRelease values (also requires the prometheus operator's `ServiceMonitor` CRD to exist or the resource to be `enabled: false`). Either approach works; pin is simpler.
- **synology-csi chart 0.11.2 references an unpublished image**: appVersion bumps to `v1.3.0` but `synology/synology-csi:v1.3.0` is `NotFound` on Docker Hub as of 2026-05-21 (likely upstream tag-not-pushed mistake or delayed release). Pinning to `0.11.1` keeps the appVersion at `v1.2.1` which IS published. Revisit when 1.3.0 is actually pushed upstream OR when a later 1.x is published.
- **Vault root token is in 1Password item id `7g4grolyien2yqkm7me2jficmy`**: Retrieve via `op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password`. Use in commands like: `kubectl exec -n vault vault-0 -c vault -- env VAULT_TOKEN=(op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password) vault operator raft list-peers`. The `-c vault` container pin matters — without it kubectl prints a `Defaulted container "vault" out of: vault, vault-data-chown (init)` warning to stdout that pollutes scripted parsing.
- **`kubectl exec` warning lines pollute parsing**: For multi-container pods, `kubectl exec` without `-c <container>` prints `Defaulted container "X" out of: X, Y` to stdout before the actual command output. Any script that captures and parses the output will see the warning as line 1. Always pin the container with `-c` for scripted use.
- **fish command-substitution collapses newlines when echoed**: `set -l var (cmd)` captures stdout as a list-of-lines, but `echo $var | grep ...` joins them with spaces — newlines lost. To preserve line structure: pipe directly without the intermediate variable (`cmd | grep ...`), or iterate the list (`for line in $var`). Bit cluster-health script parsing twice during 2026-05-21.
- **Hostkey files can be left zero-byte after a hard crash mid-write**: ext4 journal replay restores inode metadata (file exists, mtime, owner) but not data that was still in the page cache at crash time. If the interrupted write was to `/etc/ssh/ssh_host_*_key`, the running sshd keeps serving from in-memory hostkeys for hours or days (sshd loads hostkeys at start and doesn't re-read them per-connection), then dies on the next restart with `sshd: no hostkeys available -- exiting.` Diagnostic: `ls -la /etc/ssh/ssh_host_*_key` (zero-byte with old mtime), cross-reference `find /etc /var -size 0 -newermt '<window>'` for other affected files at the same timestamp. Recovery: `rm` the empties first (`ssh-keygen -A` treats `exists` as `skip` and won't overwrite zero-byte files), then `ssh-keygen -A`, then `systemctl start sshd`. Then `ssh-keygen -R <host>` + `-R <ip>` on the Ansible control node and any operator workstation, or the next play / SSH attempt fails with host-key-changed. Discovered 2026-05-21 on einherjar-urd — corruption traced to a NUC7 hard crash on 2026-05-20 07:00 that interrupted cloud-init's writes. Going-forward defense (planned): baseline role asserts hostkey files exist and are non-empty, regenerates if not.
- **`last reboot` showing multiple "still running" entries = hard crashes**: `wtmp` records boot entries when systemd starts and clean-shutdown entries when systemd stops. A boot entry without a matching shutdown reads as "still running" even though the session has ended. Only the current boot can actually be running. Multiple "still running" entries in `last reboot` output = unclean shutdowns (power loss, kernel panic, host died, hypervisor killed the VM). Useful first-pass diagnostic when something is "broken since some time ago but we don't know when" — pair with `find -newermt` to localize the crash to a window. Entries roll off naturally via logrotate (typically monthly on RHEL 9 defaults); rotated files survive one cycle as `/var/log/wtmp.1`. Don't worry about cleaning them; they're an accurate record, just misleadingly formatted.
- **iSCSI node records persist across pod migrations and outlive sessions** — distinct class from the existing single-session-LUN gotcha. Sessions (`iscsiadm -m session`) reflect active TCP connections; kubelet's CSI hooks tear these down when pods leave a node. Node records (`iscsiadm -m node`, files in `/var/lib/iscsi/nodes/`) are persistent reconnect config and are NOT cleaned up by CSI when pods migrate. After any worker-to-worker pod migration involving an iSCSI PV, the source worker is left with a stale node record for the target it no longer serves. Latent risk: on the source worker's next iscsid restart or reboot, it will attempt to log in to all known nodes, including stale ones — and may succeed, creating cross-node sessions that can block legitimate consumers via Multi-Attach errors when the kubelet later tries to schedule pods there. Diagnosis: compare `iscsiadm -m node` to `iscsiadm -m session`; entries in `node` but not `session` are orphans IF the corresponding PV is currently bound to a *different* node (`kubectl get volumeattachment` confirms which node K8s thinks is canonical). Cleanup: `iscsiadm -m node -T <iqn> -p <portal> -o delete` on the source node. **Recurs after every pod migration involving an iSCSI PV** — needs either automation or periodic manual sweeps. Discovered 2026-05-21 Phase 4a cleanup — two orphan records found on einherjar-skuld after Phase 4a's reshuffle moved vault-0 and vault-2 to other workers.
- **Proxmox orphan LVs from a host that died mid-clone**: Proxmox's `qm clone` allocates the destination LVs (`vm-NNNN-cloudinit`, `vm-NNNN-disk-0`, etc.) *before* writing the VM config. If the host crashes or freezes between LV allocation and config write — or the operator aborts mid-clone — the LVs are stranded with no VM config to own them. The Proxmox UI's "Remove" action can't clean them (it expects a VM config to act on), and `qm destroy NNNN` fails with "no such VM". Symptom on next clone attempt at the same VM ID: `lvcreate 'pve/vm-NNNN-cloudinit' error: Logical Volume "vm-NNNN-cloudinit" already exists in volume group "pve"`. Diagnosis: `qm list | grep NNNN` (confirm no VM config) + `lvs | grep vm-NNNN` (list orphans). Recovery: `lvremove -f /dev/pve/vm-NNNN-cloudinit /dev/pve/vm-NNNN-disk-0` (and any other matching LVs). If a phantom VM config DOES exist, prefer `qm destroy NNNN --purge` (which cleans LVs + config together). Discovered 2026-05-22 Phase 4b — orphans dated to the Urd-on-NUC7 era when a prior Göndul migration attempt froze mid-clone. Class is "state surviving outside the orchestrator's view" — same shape as the iSCSI node-record class; whenever the underlying host fails between resource allocation and resource registration, manual cleanup is the only path.
- **iproute 6.17 doesn't ship `/etc/iproute2/rt_tables`**: The package places the stock file at `/usr/share/iproute2/rt_tables` only. `/etc/iproute2/rt_tables` is the user-editable override and is NOT shipped — `ls -la /etc/iproute2/` on a fresh RHEL 9 + iproute 6.17 install shows only `README`. Ansible's `lineinfile` module errors with `Destination /etc/iproute2/rt_tables does not exist !` on default settings (`create: no`). Older workers may have the file by historical accident — earlier iproute versions did ship it at `/etc/`, or earlier Ansible/iproute combinations were more permissive about `create`. **Fix:** any `lineinfile` task targeting `/etc/iproute2/rt_tables` must set `create: yes` + explicit `owner: root` / `group: root` / `mode: '0644'`. Already shipped in `roles/k3s/tasks/network.yml` as of 2026-05-22. Reinforces the existing "always set owner/group/mode explicitly" rule — it applies to `lineinfile` too, not just `file`/`template`/`copy`.
- **Vault Helm chart pod anti-affinity is `requiredDuringSchedulingIgnoredDuringExecution`** by default: Vault server pods are hard-required to be on different nodes. At 3 replicas on a 3-worker cluster, every worker has exactly one Vault pod. **Implication for worker rebuilds:** cordoning a worker and deleting its Vault pod (expecting reschedule elsewhere) leaves the new pod Pending because no other worker satisfies the anti-affinity rule. Three options: (a) accept 2/3 voters during the maintenance window (fine for ~20-30 min, Vault stays fully read+write); (b) relax anti-affinity to `preferred` for the window via HelmRelease values + `flux suspend`/`resume` (significant surface area); (c) provision (N+1) workers so there's always a slot. Picked (a) for the 2026-05-22 einherjar-urd rebuild — works, Vault recovers as soon as the rebuilt worker is Ready. Documented 2026-05-22.
- **Stateful worker rebuild — step down Raft leadership BEFORE drain/delete**: When the doomed worker hosts the current Raft leader, drain or pod-delete forces a Raft election under pod-termination pressure — the dying leader can't cleanly hand off because it's being killed. `vault operator step-down` first (while the doomed pod is still healthy) lets the cluster pick a new leader gracefully, then the pod-delete is a clean follower departure. Cost ~10s. Applies to any Raft-quorum workload, not just Vault. Documented 2026-05-22.
- **Default Ansible playbook execution is parallel — a multi-node-outage footgun for K3s nodes**: Without `serial: 1` (or a similar throttling), Ansible runs tasks on all 6 K3s nodes simultaneously. A config change that triggers `restart-k3s` will fire across the entire cluster at once — defeating HA, even though Phase 4a empirically established that per-node K3s restart is safe. The `asgard-k3s.yml` playbook is currently default-parallel; pending change to make `serial: 1` the default, with explicit per-invocation override for cluster-from-zero rebuilds. Until that lands: be careful what you run with `--limit '*'`, and any config-template change should be staged with `--limit <one-node>` first.
- **MetalLB-announced VIPs do not respond to ICMP**: MetalLB L2 mode does NOT bind the VIP to any interface — it only ARPs for it. Inbound ICMP to the VIP arrives at the elected node, finds no kube-proxy DNAT rule (those only match Service-defined TCP/UDP ports), falls through forwarding, and the kernel emits ICMP Destination Host Unreachable from whichever local IP the error path picks. The symptom looks like a routing failure when in fact the VIP is healthy. **Always test MetalLB reachability with TCP on the Service's defined ports** (curl, nc) — never with ping. Discovered 2026-05-22 Phase 5e.1.
- **Traefik chart v39+ replaced shorthand entrypoint syntax with upstream-aligned nesting**: Old chart-specific shorthand (`ports.web.redirectTo`, `ports.websecure.tls.enabled`) was removed in v39 and replaced with the upstream Traefik static-config nesting (`ports.web.http.redirections.entryPoint`, `ports.websecure.http.tls: {}`). Old syntax fails schema validation with `additional properties 'redirectTo' / 'tls' not allowed`. The change consolidates on the upstream syntax to (1) match the docs, (2) eliminate chart-specific quirks, (3) make chart configuration translate 1:1 to non-chart deployments. Same theme: Service block was upstream-aligned in v40. Discovered 2026-05-22 Phase 5e.1.
- **Traefik chart has no `kubernetesIngressRoute` provider toggle**: The Traefik CRD provider is `kubernetesCRD`, which handles ALL Traefik CRDs together (IngressRoute, IngressRouteTCP/UDP, Middleware, MiddlewareTCP, TLSOption, TLSStore, ServersTransport, TraefikService). You cannot disable IngressRoute processing while keeping Middleware processing — they're one toggle. To use Gateway API only: leave `kubernetesCRD: enabled: true` for Middleware CR support (HTTPRoute ExtensionRef filters reference them), simply don't create IngressRoute CRs. Discovered 2026-05-22 Phase 5e.1.
- **Traefik Gateway listener `port:` matches the entrypoint's internal listen port, NOT the Service's `exposedPort`**: The chart's default `web` entrypoint listens on `:8000` internally (Service exposes :80 → :8000); `websecure` on `:8443` (Service exposes :443 → :8443). Gateway `listeners[].port: 80` won't match — Traefik logs `PortUnavailable` ("no matching entryPoint for port 80 and protocol HTTPS"). Two fixes: (a) set Gateway listeners to the internal ports (`port: 8000`, `port: 8443`) — conceptually muddy; (b) grant Traefik `NET_BIND_SERVICE` capability and have entrypoints bind 80/443 directly (`ports.web.port: 80, containerPort: 80` + `securityContext.capabilities.add: [NET_BIND_SERVICE]`). Chose (b) — canonical upstream pattern, no Service-port-rewrite shim, Gateway manifests read naturally. Discovered 2026-05-22 Phase 5e.1.
- **YAML key casing — schema-validation-only catches what the schema knows about**: `rollingUpdate` (correct) vs `rollingupdate` (typo) is silently dropped by Kubernetes when the parent schema doesn't define `rollingupdate` as an allowed key. The misnamed key falls into "unknown field" handling — depending on strict-vs-permissive admission, the unknown key is either dropped or rejected. With the default permissive admission for Deployment.spec.strategy, lowercased `rollingupdate` was accepted-then-ignored, and the default `maxSurge: 25%` kicked in instead of our `maxSurge: 0`. The visible symptom is "I set X but it didn't apply"; the real cause is silent key drop. **Generalize: schema validation is not a typo checker.** When debugging "my override isn't taking", `kubectl get <resource> -o yaml` and grep for the actual key — if it's missing, the manifest probably has a casing or spelling issue, not a logic issue. Discovered 2026-05-22 Phase 5e.1.
- **Required pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks on N replicas across N nodes**: With `replicas: 3`, `requiredDuringSchedulingIgnoredDuringExecution` anti-affinity at `topologyKey: kubernetes.io/hostname`, and 3 worker nodes — the cluster has exactly 3 valid slots. Default `maxSurge: 25%` rounds up to 1, meaning the Deployment controller tries to create a 4th pod *before* killing an old one during a roll. The 4th pod can't satisfy anti-affinity (no 4th node), goes Pending forever, blocks the rollout. **Fix:** explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Trade-off: during a roll you briefly run at 2/3 instead of 3/3. Acceptable when only one replica is actively serving (MetalLB L2 election). Discovered 2026-05-22 Phase 5e.1.
- **Helm rollback-on-failure removes new-values fixes, can re-deadlock on retry**: If a HelmRelease upgrade fails (timeout, validation, whatever), Flux's `install.remediation` runs `helm rollback` to the last successful revision. **But the rollback uses the old release's values verbatim** — including the *absence* of fields added in the new release. If your fix-for-the-failure was a new values field (e.g. `strategy.rollingUpdate.maxSurge: 0`), the rollback drops it, restoring the original behavior. Next upgrade attempt then re-deadlocks on the same constraint. Recovery: `flux suspend hr`, `kubectl rollout pause deploy <name>`, manually `kubectl patch` the live Deployment to match Git's intended state, `kubectl delete rs` orphans, `kubectl rollout resume deploy`, then `flux resume hr`. Do NOT unsuspend Flux until the live Deployment's strategy already matches Git — there's a race window between Flux upgrading and the Deployment controller reconciling that can re-deadlock. Discovered 2026-05-22 Phase 5e.1.
- **`install.remediation.retries: -1` for first deploys** is the right posture for unfamiliar charts. Default `retries: 3` runs `helm uninstall` on each failure, then retries — so the post-failure cluster state no longer reflects the original problem. For a brand-new chart deploy, set `-1` (or omit the block entirely) so failures stick and the actual error is inspectable. Restore to `3` after success. Originally documented for Authentik; reinforced 2026-05-22 during cert-manager + Traefik deploys.
- **Cloudflared targets backend Services by ClusterIP DNS, never by MetalLB IPs**: Cloudflare Tunnel runs as a Deployment inside K3s, establishes outbound connections to Cloudflare edge. Traffic from Cloudflare comes IN through the tunnel and Cloudflared resolves backend URLs from inside the cluster. Using ClusterIP DNS (`http://authentik-server.authentik.svc.cluster.local:80`) keeps traffic in-cluster, no MetalLB IP needed. Targeting a MetalLB-served LB IP (e.g. Traefik VIP) would create in-cluster tromboning — packets leave the pod, hit the LB IP, return to the cluster via MetalLB ARP, get DNATed to a pod IP — pointless extra hops with the same eventual destination. **Exception:** if you specifically want middleware policy (rate-limit, auth, headers) applied to externally-tunnelled traffic, target Traefik by its ClusterIP DNS (`https://traefik.traefik.svc.cluster.local:443`) with `originRequest.httpHostHeader: <fqdn>` + `noTLSVerify: true` for internal certs. Still no MetalLB IP consumed.
- **`adguardhome-sync` "Sync done" with sub-microsecond duration is not a trustworthy success signal**: The reported duration field can read as e.g. `"duration": "2.29e-07s"` (230 nanoseconds) even when wall-clock between "Start sync" and "Sync done" is hundreds of milliseconds. The 230ns appears to be the time spent on the actual PUT to the replica when there are no changes detected — i.e. "no-op sync, took no time." But it ALSO reads this way when sync THINKS there's no diff between origin and replica but the operator-visible state on origin (UI changes, on-disk config) has changed. Diagnosis when DNS rewrites aren't propagating: compare `/control/rewrite/list` directly against origin and replicas; if origin has the rewrite but sync reports no diff, the sync process has stale cached state. Workaround: restart `adguardhome-sync.service` and reduce the cron interval to actually-operational tempo. Long-term: needs proper investigation. Discovered 2026-05-22 Phase 5e.1.
- **AGH sync interval `*/30 * * * *` is too long for operational tempo**: 30-minute cron means a DNS rewrite change on origin can take up to 30 minutes to propagate to replicas. During keepalived failover the VIP can land on a replica that's still serving stale data — clients silently get old answers. Operational DNS changes need to converge in seconds, not half-hours. Recommended interval: `*/1 * * * *`. Sync is cheap and idempotent; overkill is fine. **Pending Ansible role change.** Discovered 2026-05-22 Phase 5e.1.
- **Tailscale auth keys cap at 90 days — no truly indefinite option**: Tailscale's API hard-clamps `expiry` to 7776000 seconds (90 days); the provider defaults to 90d when omitted, longer values are silently capped. For servers that need always-valid join credentials, the closest practical equivalent to "indefinite" is `reusable = true` + `recreate_if_invalid = "always"` + explicit `expiry = 7776000`. With this combo, any `terraform apply` after a key expires regenerates it (and any consumer reading the key from Vault gets the fresh value). Defensive `lifecycle { ignore_changes = [expiry] }` is also worth setting on tailscale provider < v0.29.0 — pre-PR-#521 the provider's read-back of the API-normalized expiry caused diff flap on every plan (resource looked like it wanted to recreate). Provider 0.29.0+ has the fix; we're on 0.28.0 (SDKv2; v0.29.0 was the Plugin Framework migration) so the lifecycle block stays as belt-and-braces. **Operator implication:** rebuilding a Tailscale-joined LXC 90+ days after initial key mint requires `terraform apply` on the tailscale module BEFORE re-running the Ansible role — otherwise the role reads a stale expired key from Vault and `tailscale up` fails with `auth key expired`. Same applies for any other future Tailscale-joined device. Discovered 2026-05-23 during 5e.3.e.ii planning.
- **Tailscale `tailscale_tailnet_key.description` is alphanumeric+spaces+hyphens-in-identifiers only**: Provider docs say "alphanumeric characters" but in practice the API also accepts ASCII spaces and ASCII hyphens when they appear inside identifier-like tokens (e.g. `subnet-router` works because the hyphen is mid-word). The API REJECTS parentheses, em-dashes (`—`), and forward slashes — apply fails with a generic invalid-character error rather than a precise list of offending chars, so the only way to debug is bisect-the-description. Safe template: `"<hostname> <tag> managed by terraform"` (e.g. `"bifrost subnet-router managed by terraform"`). Anything fancier — parens-around-the-tag, em-dash separators, paths like `terraform/tailscale/` — silently rejected. Discovered 2026-05-23 during 5e.3.e.ii apply.
- **Tailscale subnet routers + exit nodes need `net.ipv4.ip_forward=1` + `net.ipv6.conf.all.forwarding=1`**: `tailscale up` succeeds without these — the daemon comes up, the node joins the tailnet, the device shows as connected. But Tailscale's control plane runs a relay-capability check *per advertised route family*, and without the sysctls flags the device with "This machine is misconfigured and cannot relay traffic" in the UI. The check is **kernel-state-declarative, not functional**: setting `net.ipv6.conf.all.forwarding=1` is sufficient to satisfy it even when the LXC has only link-local IPv6 (no actual global v6 path exists). This matters specifically for `--advertise-exit-node`, which implicitly advertises both `0.0.0.0/0` AND `::/0` and therefore triggers the v6 check regardless of LAN v6 availability. The Tailscale role persists both sysctls to `/etc/sysctl.d/99-tailscale.conf` via `ansible.posix.sysctl`. Tailscale's own subnet-router setup docs (https://tailscale.com/kb/1019/subnets#enable-ip-forwarding) call this out — easy to miss because the daemon doesn't enforce it. Discovered 2026-05-23 during 5e.3.e.v validation; the v6 sysctl was missed on initial apply and only added once gjallarbru kept flagging "cannot relay" after the v4 sysctl alone wasn't enough.
- **Verify a proof-of-pattern actually demonstrates the pattern before mirroring it**: When a doc reference (in CLAUDE.md or homelab-design.md) cites a file/role/module as the canonical example of a pattern, grep the actual file before mirroring — don't trust the doc reference at face value. Discovered 2026-05-23 during 5e.3.e.iv: CLAUDE.md cited `secret/ansible/sftpgo/admin-password` as proof-of-pattern for AppRole Vault lookup, but the actual SFTPGo role does local `pwgen` + on-disk file (the migration was planned but never landed). The real proof-of-pattern was `ansible/playbooks/test-vault-lookup.yml`, which actually demonstrates the lookup. Cost: invented `vault_url`/`role_id`/`secret_id` variable names that don't exist in the repo; one play-failure-and-retry cycle to discover. Generalized rule: when about to mirror an existing pattern, `grep -r` the actual usage before drafting; doc references can drift from reality.
- **Tailscale DSM 7 package upgrades wipe the `configure-host` TUN-permissions state**: Per Tailscale's Synology docs (https://tailscale.com/docs/integrations/synology#enable-outbound-connections), upgrading the Tailscale package on DSM 7 removes the TUN device permissions previously installed by `tailscale configure-host`. The Boot-up Task Scheduler task (`tailscale-tun-permissions` in our setup) re-applies the config on next reboot — but if the package auto-updates without a reboot, subnet-router and exit-node functionality silently break until either a reboot or a manual re-run of the configure-host script. **Diagnostic**: device shows up in tailnet admin but flags "cannot relay traffic" / "machine is misconfigured"; SSH-side `tailscale netcheck` shows no relay capability. **Recovery**: SSH in and run `/var/packages/Tailscale/target/bin/tailscale configure-host; synosystemctl restart pkgctl-Tailscale.service` as root, OR reboot the NAS (note: reboot is K3s-fragile while iSCSI PVCs are attached — see ext4-journal-abort gotcha above; prefer the manual script). Going forward: consider disabling Tailscale auto-update in DSM Package Center so upgrades become operator-driven, paired with explicit configure-host re-run. Documented 2026-05-23 during 5e.3.f from Tailscale's official Synology docs.
- **Proxmox same-node hardware refresh (disk transplant) → NIC name changes break network on boot**: Physical NICs on different motherboards get different predictable interface names. Beelink MINI-S12 UEFI labels its NIC `nic0` (vendor-custom); MSI Cubi (i3-1215u) uses standard predictable naming `enp45s0`. `/etc/network/interfaces` references the old name → after the swap, `vmbr0` has no bridge port and the node boots with no LAN. Cluster sees node as offline indefinitely. **Recovery requires physical console access** (USB keyboard + HDMI), not SSH. Pre-flight: capture `ip -br link` on old hardware. Post-boot fix: `ip -br link` to get new name → vim `:%s/<old>/<new>/g` in `/etc/network/interfaces` → `ifreload -a`. **Identical hardware models yield identical predictable names** — Urd and Verd are both Cubi i3-1215u, both ended up as `enp45s0`, so any future Beelink → Cubi refresh on Skuld will hit the same `nic0` → `enp45s0` rename. Discovered Phase 4c 2026-05-23; was silently hit during the Urd refresh too but not documented at the time.

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns. K8s-specific concepts are the knowledge gap. Deep K3s/K8s experimentation is intended for the future jotunheim ("can implode") cluster — asgard is built carefully, not used as a learning sandbox.