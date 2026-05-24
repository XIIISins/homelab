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

**Hardware notes:** *(Migration narratives + storage-tier verification live in `docs/homelab-design.md` Hardware section + Build sequence rows 4a/4b/4c + Incident log.)*
- **Storage tier — etcd fsync consistency: Verd ≈ Skuld > Urd.** Urd's NVMe is DRAM-less HMB Gen 4 (slowest under sustained sync); Verd + Skuld are DRAM-equipped Gen 3. All three well within etcd's tolerance, massive improvement over the old Urd mSATA.
- **Urd and Verd are now identical hardware** (MSI Cubi, i3-1215u, 32 GB DDR4) after Phase 4c (2026-05-23). Skuld is the N100/16GB outlier.
- **Urd long-term plan:** dedicated Jellyfin LXC with Intel QuickSync passthrough (i3-1215u UHD Graphics ≫ N5095 UHD). Currently also runs Einherjar-urd (K3s worker) and Factorio LXC (1120).
- **CP topology (Phase 4b, 2026-05-22):** Göndul on Urd, Hlökk on Verd, Sigrún on Skuld. The 2026-05-14 "never run CP on Urd" rule is retired — the Urd hardware refresh on 2026-05-21 removed the root cause (slow N5095 + mSATA fsync).
- **CP sizing:** 2vCPU/4GB symmetric across all three. Identical-across-nodes by rule — failover symmetry requires it (same rule as PG nodes).
- **CP-only workload posture (Phase 4a, 2026-05-21):** All three CPs tainted `node-role.kubernetes.io/control-plane=:NoSchedule`. Workload pods cannot land on them. K3s-shipped components + DaemonSets that tolerate the taint (Calico-node, kube-proxy, metallb-speaker) remain. 4 GiB is the correct CP size with this posture. **Footgun:** evicting CSI from CPs while stateful pods are still on them breaks the unmount path — see "CSI eviction footgun" gotcha.
- **2026-05-14 incident lessons preserved:** per-component config Kustomizations, idempotency, sub-kustomization-per-component. Hardware root cause is gone; structural rules stay.

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
| Asgard PG HAProxy/etcd trio | Hlin, Eir, Snotra | HAProxy + etcd-DCS fronting the PG cluster. Continued Frigg's-handmaidens theme by function: Hlin (protection — HAProxy traffic gate), Eir (healing/recovery — failover restoration), Snotra (wisdom/decision — etcd consensus). |
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

*Full phase narratives + deploy commentary in `docs/homelab-design.md` Build sequence + Incident log. This list is the at-a-glance state.*

**Infra layer 0 (foundation):**
- ✅ UCG-Ultra — VLANs, zones, firewall
- ✅ KPN DMZ → UCG-Ultra (IPv4 + IPv6)
- ✅ Synology (Munin) — factory reset, volumes, NFS, kubernetes user, Tailscale subnet router (5e.3.f)
- ✅ Proxmox cluster `niflheim` (Urd/Verd/Skuld, PVE 9.x)
- ✅ PBS — LXC 1101 on Skuld, NFS datastore

**Asgard K3s — core infrastructure:**
- ✅ Asgard K3s cluster — fully IaC. Teardown+rebuild validated 2026-05-17.
- ✅ Sealed Secrets — master keys backed up to 1Password (2026-05-17). Loss makes every SealedSecret undecryptable.
- ✅ Synology CSI — iSCSI only, `synology-csi-iscsi-retain` (default)
- ✅ Vault — 3-node Raft HA, AWS KMS auto-unseal, iSCSI. Root token + recovery keys in 1P (`asgard-rebuild-2026-05-17`). K8s + AppRole auth + KV + 2 AppRole roles in `terraform/vault/`.
- ✅ External Secrets Operator — ClusterSecretStore `vault` Ready
- ✅ MetalLB — L2 end-to-end. All four landmine fixes in IaC (see Known gotchas).
- ✅ tigera-operator — `mtu: 1450` workaround for upstream #7851 (2026-05-15)
- ✅ Phase 4a — CP workload isolation (NoSchedule taint, 2026-05-21). `node-taint:` config is registration-time only; existing CPs tainted via kubectl. Helm pins concrete-pinned at this point after two floating-pin outages.
- ✅ Urd hardware refresh — MSI Cubi (2026-05-21). Original etcd-storm root cause removed.
- ✅ Phase 4b — Göndul Verd → Urd (2026-05-22). CP topology now Göndul/Hlökk/Sigrún on Urd/Verd/Skuld.
- ✅ Einherjar-urd worker rebuild — TF `template_node` correction + worker-rebuild-path validation (2026-05-22). Vault 2/3 voters during window by design.
- ✅ Phase 4c — Verd hardware refresh (MSI Cubi, 2026-05-23). Both Cubi nodes now identical; cluster stayed 3/3 throughout. Surfaced reboot-test persistence-validation rule (now in process expectations).

**Asgard K3s — edge stack + services:**
- ✅ Phase 5e.1 — Traefik + Gateway API + cert-manager (2026-05-22). Gateway API v1.5.1, cert-manager v1.19.0, Traefik v40.2.0/v3.7.1. Wildcard `*.niflheim.xiiisins.com` (ECDSA, LE prod, DNS-01).
- ✅ Phase 5e.2 — Cloudflared + apex zone + WebFinger (2026-05-23). Cloudflared 2026.5.0 (3 replicas, locally-managed tunnel, credentials via ESO from Vault). Wildcards `*.midgard.xiiisins.com` + `*.xiiisins.com`. Second `midgard` Gateway with three HTTPS listeners. WebFinger via Caddy pod in `apps/apex-static/`. Authentik now at `authentik.xiiisins.com` + `authentik.midgard.xiiisins.com`.
- ✅ Phase 5e.3 — Tailscale OIDC blueprints + LXCs (2026-05-23). `terraform/authentik/` (identity-as-data via `users.yaml`/`groups.yaml`) + `terraform/tailscale/` (ACL grants syntax, `10.0.0.0/16` supernet auto-approve) + LXCs 1113/1114/1115 (Bifrost/Heimdall/Gjallarbru). Tailscale Free plan, split-auth (TF-minted auto-renewing keys for servers, OIDC for user devices, 1P out-of-band for Munin).
- ✅ Factorio LXC (1120) — operator self-service via SFTPGo, reconcile loop (2026-05-16)
- ✅ Fulla / PostgreSQL 1 (LXC 1130) — PG 17, TLS, scram-sha-256, two SUPERUSER management roles in Vault (2026-05-17). Standalone; cluster expansion in flight via Phase 5g.2.
- ✅ Authentik + Redis — Authentik 2026.2.3 + hand-rolled Redis StatefulSet (2026-05-17). Now exposed via Traefik+Gateway+Cloudflared.

**Pending:**
- 🟡 AdGuard Home — functionally ✅ (Saga/Mimir/Kvasir, VIP 10.0.10.200, sync) but **manually installed, no Ansible role**. Sync bumped `*/30` → `*/1` cron. Phase 5b.2 — AdGuard IaC — pending (Terraform LXC + Ansible roles).
- 🟡 Phase 5g.2 — PG HA with Patroni. Done: design + decision rows (2026-05-24), HAProxy/etcd trio + Vör/Idunn PG LXCs provisioned + baseline (1131-1135), etcd 3-node cluster bootstrapped on Hlin/Eir/Snotra (Snotra leader). Remaining: Patroni Fulla adoption + replica join → HAProxy + keepalived VIP `10.0.10.210` → Authentik cutover from IP-stopgap → failover validation. Step-list checkboxes tracked in `docs/homelab-design.md` pending tasks. (Phase 5g.2 commits 3ad9077/6b9830e/f83657a were originally labelled `5g.2.a/b/c` — those subjects stay as historical record per the rule, but the L4 letters are not the canonical sub-phase tracking.)
- 🔲 Remaining asgard LXCs (Teamspeak, Zabbix, Jellyfin)
- 🔲 Jotunheim K3s
- 🔲 Services (Outline, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Netbox, n8n, Privatebin, Startpage)

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

**Control planes (Göndul/Hlökk/Sigrún):** 2 vCPU / 4GB / 10GB disk each, single NIC on VLAN 21, RHEL 9 + K3s + Calico. Göndul on Urd, Hlökk on Verd, Sigrún on Skuld. Tainted `node-role.kubernetes.io/control-plane=:NoSchedule` (Phase 4a). See Hardware notes for sizing/taint rationale.

**Workers (Einherjar-urd/verd/skuld):** 2 vCPU / 4GB / 15GB disk, eth0 on VLAN 21, eth1 on VLAN 20 (MetalLB L2 — IPs 10.0.20.201/202/203), RHEL 9 + K3s + Calico + iscsiadm.

⚠️ **Workers are multi-homed.** The eth1/VLAN 20 NIC requires four landmine fixes — all in IaC: Calico autodetection pin (`cidrs: ["10.0.21.0/24"]`), `rp_filter=2` (loose), `route_localnet=1`, and VLAN 20 source-based policy routing. See Known gotchas.

---

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- **Versions: concrete-pin all IaC** — Helm charts, Terraform providers, Ansible role versions. Minor-floats (`~> 4.0`, `0.x`, `2.x`) banned. Updates are deliberate ops; Renovate deferred until stable state. Known pending tighten: `terraform/vault/` `~> 4.0`. Why-this-rule history (two floating-pin outages on 2026-05-21) in design doc decision rows 909/910 + Known gotchas below.
- Conventional commits
- Norse mythology naming
- **File-path header** — every source file in the repo starts with a comment line containing its repo-relative path (e.g. `# k8s/asgard/apps/apex-static/configmap.yaml`). Lets `grep` on a known path-string find moved files even after a `git mv`. Applies to all `.tf`, `.yaml`, `.yml`, `.j2`, `.sh`, `.fish`, `.py` files. Existing pre-convention files: retroactive sweep pending (see Pending tasks in `docs/homelab-design.md`).
- Never commit secrets
- **Terraform state is never committed.** `*.tfstate`, `*.tfstate.backup`, and `.terraform/` are gitignored across every `terraform/*/` module. State contains decrypted secrets (any `random_password`, any `vault_kv_secret_v2` `data_json`, etc.), generated credentials, and resource IDs that count as identity attestation — committing it would invert every Vault/1Password discipline in this repo. State lives on the operator workstation. Backup is the operator's responsibility (1Password attachment, encrypted external drive); long-term move to a Vault KV-backed remote backend is on the roadmap.
- Shell: fish (owner uses fish shell — heredocs `<<EOF` don't work, use pipes or temp files)

---

## Known gotchas

*Incident retrospectives and discovery dates live in `docs/homelab-design.md` Incident log. These entries are the rules + recovery commands.*

### Networking / multi-homed workers

- **Calico autodetection on multi-homed workers**: default `firstFound` binds the overlay to eth1 (VLAN 20), breaking cross-node vxlan. Fix in Ansible Calico template: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]`. Do NOT revert to firstFound.
- **rp_filter must be loose (`2`)** on multi-homed workers. Strict mode silently drops MetalLB LoadBalancer traffic arriving on eth1. `roles/k3s/tasks/network.yml` sets it on `all` + `default`. Do NOT "harden" back to 1.
- **route_localnet=1** required. MetalLB L2 only ARPs for the VIP — without `route_localnet=1` the kernel drops packets to the unbound IP. Set on `all` by `roles/k3s/tasks/network.yml`.
- **VLAN 20 source-based policy routing.** Reply packets from MetalLB VIPs originate from worker eth1 IP; without policy routing they exit via eth0 → asymmetric → UCG stateful firewall drops. Fix: `vlan20-policy-routing.service` systemd oneshot (`from 10.0.20.0/24 lookup vlan20` + `default via 10.0.20.1 dev eth1 table vlan20`). OS-independent (pure ip(8) + systemd).
- **MetalLB L2 election needs `nodeSelectors`** excluding CP nodes (CPs have no eth1 — election there announces nowhere).
- **MetalLB-announced VIPs do not respond to ICMP.** L2 only ARPs; ICMP-to-VIP hits the elected node with no kube-proxy DNAT → kernel returns Destination Host Unreachable. **Test reachability with TCP** (curl/nc on a defined port), never ping.
- **Calico `Installation` CR is merged by the K3s addon controller**, not replaced. Removed fields can persist. After autodetection changes verify with `kubectl get installation default -o jsonpath='{.spec.calicoNetwork.nodeAddressAutodetectionV4}'` and `kubectl patch --type=json` if stale fields linger.
- **tigera-operator `/var/lib/calico/mtu` denied (upstream #7851).** SELinux MCS mismatch (operator `s0:c322,c902` vs file `s0` no-categories). `dontaudit`'d so `ausearch` silent until `semodule -DB`. **Fix:** explicit `mtu: 1450` in `Installation` CR (1500 - 50 VXLAN). Operator never reads the file when MTU is set. Do NOT remove without replacement fix.
- **Proxmox same-node hardware refresh changes NIC names.** Beelink `nic0` (vendor-custom UEFI) vs MSI Cubi `enp45s0` (predictable). `/etc/network/interfaces` references the old name → no LAN after boot. **Requires physical console** to fix. Pre-flight: capture `ip -br link` on old hardware. Post-boot: vim `:%s/<old>/<new>/g` + `ifreload -a`. Identical hardware → identical names (any future Beelink→Cubi refresh on Skuld will hit `nic0`→`enp45s0`).

### Storage / iSCSI / Synology CSI

- **iSCSI LUNs are single-session.** After ungraceful restarts, stale sessions or discovery node records can pin a LUN to the wrong node (`non-retryable iSCSI login failure` / `iscsi_limit_max_session_count`). Cleanup: `iscsiadm -m session` / `-m node -o delete` on affected nodes; clear NAS-side if needed.
- **iSCSI node records persist across pod migrations and outlive sessions.** Sessions die with the pod; `/var/lib/iscsi/nodes/` records do not. After worker-to-worker iSCSI PV migration, source worker is left with stale node records. Latent risk: next iscsid restart/reboot logs in to stale targets → Multi-Attach errors elsewhere. Diagnosis: `iscsiadm -m node` vs `iscsiadm -m session`; entries in `node` but not `session` AND the PV is bound to a *different* node = orphan. Cleanup: `iscsiadm -m node -T <iqn> -p <portal> -o delete` on source. **Recurs after every iSCSI PV migration.**
- **iSCSI session timeout → ext4 journal abort → FS RO.** Network blip during maintenance lets the session time out; ext4 remounts RO. Recovery: scale workload to 0, attach LUN to debug pod, `fsck.ext4 -y /dev/sdX` (from Alpine — see RHEL 9 e2fsprogs gotcha below), detach, scale back up.
- **Released PVs with retain policy leave orphan iSCSI sessions** on the worker that last consumed them. Consumes Synology's `iscsi_limit_max_session_count`. Cleanup: `iscsiadm -m node -T <iqn> -p 10.0.254.20 --logout` then `-o delete`, plus NAS-side LUN cleanup via DSM. asgard-health script cross-references live sessions against `kubectl get pv` to flag orphans.
- **Synology DSM-side stale state can block iSCSI re-attach.** If a VolumeAttachment is deleted but DSM still considers a previous initiator connected, re-attach times out. Check DSM SAN Manager → Target → Connected Initiators; manually disconnect stale entries.
- **CSI controller down → all VolumeAttachment ops stall cluster-wide.** Single-replica StatefulSet, no redundancy. First diagnostic for multi-pod `Init:0/1` hangs: `kubectl get pods -n synology-csi -o wide` and check controller status.
- **Synology CSI iSCSI volumes need a chown initContainer for non-root pods.** Synology CSI doesn't honor `securityContext.fsGroup` reliably. Pattern: busybox initContainer as UID 0 running `chown -R <uid>:<gid> /mountpath` before the main container. Currently shipped in Vault (`vault-data-chown`) + Authentik Redis (`redis-data-chown`).
- **Vault SKIP_CHOWN + iSCSI fsGroup quirk.** Chart sets `SKIP_CHOWN=true` when non-root; fsGroup doesn't apply on iSCSI. Pod-level `securityContext { runAsUser: 100, runAsGroup: 1000, fsGroup: 1000, runAsNonRoot: false }` + `extraInitContainer vault-data-chown` (busybox UID 0) running `chown -R 100:1000 /vault/data && chmod 750 /vault/data`.
- **RHEL 9 e2fsprogs 1.46.5 cannot fsck Synology-CSI-formatted iSCSI LUNs.** CSI uses newer ext4 features (`orphan_file`, ro-compat bit 16) → `unsupported feature(s): FEATURE_C12 FEATURE_R16`. Workaround: fsck from Alpine 3.20 debug pod. `kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh` then `apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX`. Revisit at RHEL 10.
- **CSI eviction from tainted CPs is a footgun**, not a free side effect. CP NoSchedule taint evicts the CSI node-plugin DaemonSet; stateful pod still on the CP can't unmount (kubelet retries forever — `csi.san.synology.com not found`). `VolumeAttachment` stays `ATTACHED=true NODE=<dead-CP>` → Multi-Attach errors elsewhere. **Mitigation:** drain stateful workloads BEFORE applying CP taint, OR add CP-taint toleration to CSI DaemonSet. Recovery: iscsiadm logout from affected CP via debug pod, force-delete pod, manually delete stale VolumeAttachment.

### K3s lifecycle / rebuilds

- **CP rebuild → "duplicate node name found"**: `kubectl delete node <name>` from a surviving CP *before* starting K3s on the new VM. The K3s node-delete handler also evicts the stale etcd member.
- **CP rebuild of the default init node**: override with `ansible-playbook playbooks/asgard-k3s.yml --limit <name> -e 'k3s_init_node=hlokk'` (any healthy CP). Otherwise the role `--cluster-init`s a fresh cluster.
- **K3s role install-skip on healthy nodes.** `detect-state.yml` sets `k3s_already_healthy` if `is-active k3s == active` AND node `Ready` → `install.yml` + `calico.yml` skipped. `config.yml` is separate and ALWAYS runs (split out 2026-05-21 — config-template was previously bundled with install.yml and never rendered on healthy nodes).
- **K3s `node-taint:` config is registration-time only.** Restarting K3s on an existing cluster member does NOT re-apply taints. For existing nodes: `kubectl taint node <name> ...`. Config-template change still matters for fresh bootstraps.
- **K3s restart on existing CP**: empirically does NOT trigger duplicate-node-name in steady state (caveat, not blocker — only fresh-bootstrap is at risk).
- **`NoSchedule` taint does NOT evict existing workload pods** — only blocks new scheduling. DaemonSets respect taints automatically; Deployments/StatefulSets stay until natural churn or explicit `kubectl delete pod`. Don't taint a CP with stateful pods expecting them to move.
- **Vault Helm chart uses required (not preferred) pod anti-affinity.** 3 replicas × 3 workers = exactly 3 slots. Cordoning a worker leaves the displaced Vault pod Pending. Accept 2/3 voters for ~20-30 min during single-worker rebuilds (Vault stays fully read+write).
- **Step Raft leadership BEFORE drain/delete** on the doomed worker. `vault operator step-down` first (while pod healthy) lets the cluster elect cleanly; pod-delete after is a clean follower departure. Cost ~10s. Applies to any Raft-quorum workload.
- **Default Ansible playbook execution is parallel** — multi-node-outage footgun for K3s. A config change triggering `restart-k3s` fires across all 6 nodes at once. Pending: `serial: 1` default in `asgard-k3s.yml`. Until then: stage config changes with `--limit <one-node>` first.

### Vault

- **Vault TLS disabled is deliberate.** `tls_disable = 1` on listener (cluster traffic too). Conscious homelab tradeoff. `vault-ui` LB on `10.0.20.11` serves plaintext HTTP — internal/VLAN-only.
- **Vault Raft follower auto-join can fail** on fresh init. If vault-1/2 missed the join window, they log "stored unseal keys are supported, but none were found." Fix: `kubectl exec -n vault vault-<N> -- vault operator raft join http://vault-0.vault-internal:8200`. Auto-unseal via KMS after.
- **Vault init can leave stuck partial state.** Recovery: `kubectl delete statefulset vault -n vault --cascade=orphan`, `kubectl delete pvc -n vault data-vault-{0,1,2}`, `kubectl delete pod -n vault vault-{0,1,2} --force --grace-period=0`, then `flux reconcile helmrelease vault -n vault --force`. Clean up old iSCSI LUNs on DSM after.
- **Vault root token is in 1Password item `7g4grolyien2yqkm7me2jficmy`.** Retrieve: `op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password`. Use with container pin: `kubectl exec -n vault vault-0 -c vault -- env VAULT_TOKEN=(op item get 7g4grolyien2yqkm7me2jficmy --reveal --fields password) vault operator raft list-peers`.

### Flux / Helm / Kustomize

- **CRD timing — SealedSecrets (and any CRD-dependent resource)** cannot live in the same Kustomization that installs the CRD. The Kustomization dry-run runs before chart install. Pattern: per-component `<component>-config/` Kustomization with `dependsOn: infrastructure`. Current: `infrastructure-config`, `metallb-config`, `vault-config`, `synology-csi-config`.
- **Sealed-secrets master keys must be backed up to 1P after every controller install.** Loss → every SealedSecret in Git undecryptable. Backup: `kubectl get secret -n sealed-secrets -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml > backup.yaml`. Restore: `kubectl apply -f backup.yaml` *before* sealed-secrets controller starts on the rebuilt cluster.
- **Flux deploy key not in IaC.** `flux bootstrap github` recreates idempotently if the GitHub deploy key still exists; otherwise mints a fresh one. NOT a SealedSecret.
- **ESO API version**: `external-secrets.io/v1` (not `v1beta1`).
- **MetalLB speaker labels**: `app.kubernetes.io/component=speaker` (not `component=speaker`).
- **HelmRelease `install.remediation.retries: -1` for first deploys.** Default `retries: 3` runs `helm uninstall` on failure → post-failure state doesn't reflect original problem (ESO finalizers can also stick uninstall). Set `-1` (or omit block) for first deploy; restore `3` after success. Extend `spec.timeout` past 5min for slow first reconciles (Authentik wants ~15min).
- **Failed Helm reconcile recovery:** `flux suspend hr` → `helm rollback <name> <last-good-rev> -n <ns> --wait=false` → verify pods → `kubectl delete pod` to force template re-render if needed → fix `spec.chart.spec.version` in Git → `flux resume hr` → `flux reconcile hr`.
- **Helm rollback-on-failure removes new-values fixes** → can re-deadlock on retry. Rollback uses old release values verbatim; new-values fields are dropped. Recovery: `flux suspend hr`, `kubectl rollout pause deploy <name>`, `kubectl patch` live Deployment to match Git, `kubectl delete rs` orphans, `kubectl rollout resume deploy`, then `flux resume hr`. Don't unsuspend until live Deployment matches Git or the race re-deadlocks.
- **Kustomize `configMapGenerator` with `files: []` fails kustomize build.** Don't keep an empty generator as placeholder; omit until the first file lands.
- **Kustomize-managed ConfigMaps referenced by static name need `disableNameSuffixHash: true`.** Default appends hash → static-name consumers (e.g. Authentik `blueprints.configMaps: [authentik-blueprints]`) can't resolve. Updates mutate in-place; consumers reconcile on their own loop.
- **CoreDNS forwards to `/etc/resolv.conf` from each replica's node.** Every node needs a working resolv.conf. Node resolv.conf changes need `kubectl rollout restart deploy -n kube-system coredns` to take effect.
- **Concrete-pin all IaC versions, no floats.** Helm + TF providers + Ansible role versions. `~> 4.0` / `0.x` / `2.x` all banned — they cover patch versions which proved breaking (metallb 0.16 unconditional ServiceMonitor; synology-csi 0.11.2 → unpublished `v1.3.0` image). Current asgard pins: sealed-secrets 2.18.6, vault 0.32.0, external-secrets 0.20.4, metallb 0.15.3, synology-csi 0.11.1, authentik 2026.2.3.
- **MetalLB chart 0.16+ requires explicit `prometheus` values block.** Unconditional `.Values.prometheus.serviceMonitor.enabled` reference; template render fails without it. Either pin `0.15.x` or set `prometheus.serviceMonitor.enabled: false`.
- **synology-csi chart 0.11.2 references unpublished `synology/synology-csi:v1.3.0`** on Docker Hub. Pin `0.11.1` (appVersion `v1.2.1`). Revisit when 1.3.0 actually ships.

### K8s scheduling

- **Required pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks on N replicas across N nodes.** Default `maxSurge: 25%` rounds up to 1; cluster has no 4th slot → rollout deadlocks. Fix: explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Briefly runs at N-1/N during rolls.
- **YAML key casing is silently dropped** when unknown to the schema (e.g. `rollingupdate` vs `rollingUpdate`). Default permissive admission accepts-then-ignores. Debug "my override isn't taking" with `kubectl get ... -o yaml` and grep for the actual key — if missing, it's a casing/spelling issue, not logic.

### Traefik / Gateway API

- **Traefik chart v39+ replaced shorthand entrypoint syntax with upstream nesting.** Old `ports.web.redirectTo` / `ports.websecure.tls.enabled` removed → use `ports.web.http.redirections.entryPoint` / `ports.websecure.http.tls: {}`. Service block upstream-aligned in v40.
- **Traefik chart has no `kubernetesIngressRoute` toggle.** `kubernetesCRD` handles ALL Traefik CRDs together (IngressRoute, Middleware, etc). To use Gateway API only: keep `kubernetesCRD: enabled: true` for Middleware CR support, just don't create IngressRoute CRs.
- **Traefik Gateway listener `port:` matches the entrypoint's internal listen port**, NOT the Service `exposedPort`. Either set Gateway listeners to chart defaults (`8000`/`8443`) — muddy — or grant `NET_BIND_SERVICE` and bind 80/443 directly. We use the latter; Gateway manifests read naturally.

### Authentik

- **Authentik chart values block does NOT override env vars** — env vars win silently. Set service config via ExternalSecret env vars (`AUTHENTIK_POSTGRESQL__HOST`/`__SSLMODE`/`__PORT`/`__USER`/`__NAME`), treat values block as default-documentation only.
- **Authentik brand `default: true` is mutually exclusive cluster-wide.** Blueprint must demote shipped `authentik-default` (set `default: false`) before claiming default on your own. Both entries can co-exist in the same file; blueprints apply in document order.
- **Authentik blueprints — brand for branding, NOT tenants.** Don't include `authentik_tenants.tenant` unless you genuinely want multi-tenant Postgres-schema isolation. Single-instance deploys conflict with the implicit default tenant.
- **Authentik OIDC discovery is per-app under `/application/o/<slug>/.well-known/openid-configuration`**, NOT host root. `https://authentik.<host>/.well-known/openid-configuration` is a hard 404. For WAF/CDN skip rules: use `/application/o/*` prefix (covers per-app discovery + authorize + token + userinfo + jwks + end-session).

### Cloudflare / Cloudflared

- **Cloudflared targets backend Services by ClusterIP DNS, NEVER MetalLB IPs.** Cloudflared runs in-cluster; ClusterIP DNS keeps traffic in pod network. MetalLB-IP targeting creates in-cluster tromboning. **Exception:** to apply Traefik middleware to externally-tunnelled traffic, target Traefik's ClusterIP DNS with `originRequest.httpHostHeader: <fqdn>` + `noTLSVerify: true`.
- **Cloudflare Free plan `Bot Management:Edit` is a separate API token scope** — NOT folded under `Zone Settings:Edit`. The `cloudflare_bot_management` TF resource requires its own `Zone:Bot Management:Edit`. Current `terraform-cloudflare` token scopes: `Zone:DNS:Edit + Zone:Zone:Read + Zone:Zone Settings:Edit + Zone:WAF:Edit + Zone:Bot Management:Edit` (all on `xiiisins.com`) + `Account:Cloudflare Tunnel:Edit`.
- **Cloudflare provider v5 ruleset import requires `zones/` discriminator prefix.** v4 accepted `<zone_id>/<ruleset_id>`; v5 demands `zones/<zone_id>/<ruleset_id>` (or `accounts/<account_id>/<ruleset_id>`). Error is explicit: `invalid discriminator segment`.

### DNS

- **Public resolvers as DNS fallback poison internal-zone resolution.** Cloudflare/Google return NXDOMAIN for `*.niflheim.xiiisins.com`; glibc + CoreDNS cache NXDOMAIN as authoritative. Fix in `roles/baseline/tasks/main.yml`: `manage_resolv_conf: false` cloud-init drop-in + templated `/etc/resolv.conf` from `baseline_nameservers`. Fallback resolvers may point at peer AdGuard (`10.0.11.201/202/203`), NEVER public.
- **AGH sync interval `*/30 * * * *` is too long** — 30min lag during keepalived failover serves stale answers. Recommended `*/1`. Sync is cheap + idempotent. Pending Ansible role change.
- **`adguardhome-sync` "Sync done" with sub-microsecond duration is NOT trustworthy.** 230ns reads as "no diff" — sometimes accurate, sometimes stale-cached. Diagnose by comparing `/control/rewrite/list` on origin vs replicas directly; if origin has the rewrite but sync reports no diff, restart `adguardhome-sync.service`.

### Postgres

- **PG `hostssl`-only rejects plaintext clients with same error as disallowed CIDR** — `FATAL: no pg_hba.conf entry for host "X", user "Y", database "Z", no encryption`. The "no encryption" suffix is the giveaway. Always set explicit `sslmode=require` on clients; libpq default `prefer` silently downgrades.

### Ansible / roles

- **`group_vars/all/`** lives at `ansible/inventory/group_vars/all/` (next to inventory, not next to playbooks — discovered only when adjacent to inventory or playbook dirs). `k3s_token` in `vault.yml` (role default empty).
- **`-u root` does NOT override `ansible_user` from group_vars.** Group_vars outrank CLI `-u`. Use `-e 'ansible_user=root'` — `-e` is the only level above inventory.
- **`--check` doesn't validate file ownership** — only existence. Bugs survive `--check`. **Always set `owner`/`group`/`mode` explicitly** on every `file`/`template`/`copy`/`lineinfile` task; never rely on defaults.
- **`ansible.posix.authorized_key` in `--check` needs explicit `path:`** + `manage_dir: true`. Module getent-resolves home; previous create-user task is a no-op in `--check` → error. Real-run is functionally identical.
- **`ansible.builtin.user` default `password: '!'` triggers PAM account lock** even for pubkey auth (under `UsePAM yes`). Fix: `password: '*'` (valid form, no usable password, key auth permitted). Required for any key-auth-only user.
- **Service-specific systemd drop-in dirs** (`/etc/systemd/system/<unit>.service.d/`) don't pre-exist. `file: state: directory` first before dropping override.conf.
- **baseline role updates all packages** (`dnf update "*"` + reboot) on every run. Not pure config-idempotency — re-runs can pull OS updates. Known/intended.
- **Minimal Proxmox Debian template lacks** `sudo`, `acl`, `tzdata`, `gnupg`, `ca-certificates`, locales. Baseline owns the class — installs all of them OS-agnostically. List grows as new roles surface new gaps. Any role with third-party apt repo needs `gnupg` + `ca-certificates`; locale-sensitive tools (PG `pg_createcluster`) need locale generated first.
- **Rancher SELinux repo path** uses `centos/8/noarch` on RHEL 9. Intentional — RPM is noarch and Rancher's documented path.
- **SELinux `dontaudit` hides denials.** Empty `ausearch` does NOT mean SELinux is innocent. Diagnose: `semodule -DB` (disable dontaudit, keep enforcing), reproduce, capture AVC, `semodule -B` to re-enable. Never leave dontaudit disabled.
- **iproute 6.17 doesn't ship `/etc/iproute2/rt_tables`** — only `/usr/share/iproute2/rt_tables`. `lineinfile` errors with `Destination ... does not exist`. Fix: `create: yes` + explicit `owner: root` / `group: root` / `mode: '0644'`. Already shipped in `roles/k3s/tasks/network.yml`. Reinforces the "always set owner/group/mode" rule — applies to `lineinfile` too.
- **Verify proof-of-pattern actually demonstrates the pattern before mirroring.** Doc references drift. `grep -r` actual usage before drafting. (CLAUDE.md cited `secret/ansible/sftpgo/admin-password` for AppRole lookup; actual SFTPGo role still uses local `pwgen`. Real proof-of-pattern is `ansible/playbooks/test-vault-lookup.yml`.)

### LXC / Proxmox

- **bpg/proxmox API token can change `nesting`, NOT other LXC features.** `keyctl`, `fuse`, `device_passthrough` require `root@pam` — `device_passthrough` also at *create*-time. Workaround: aliased provider pattern (`provider "proxmox" { alias = "root", username = "root@pam" }` with `PROXMOX_VE_PASSWORD`; declare `configuration_aliases = [proxmox.root]` in `versions.tf`; resources needing these features use `provider = proxmox.root`). Don't put `keyctl: false`/`fuse: false` in resource block — they're defaults, omitting avoids future 403s.
- **Systemd 257 in unprivileged LXC requires `nesting=true`.** Debian 13 systemd uses namespace ops needing CAP_SYS_ADMIN in user-ns. Flag name is misleading: does NOT enable nested containerization — that's preserved by `unprivileged=true`, not `nesting=false`.
- **bpg/proxmox may reboot VMs on apply** due to IP state drift — cluster should survive rolling restarts.
- **Proxmox orphan LVs from a host that died mid-clone.** `qm clone` allocates destination LVs *before* writing VM config. Crash between → stranded LVs, no config to own them. Symptom on next clone at same VM ID: `lvcreate ... already exists`. Diagnosis: `qm list | grep NNNN` (no config) + `lvs | grep vm-NNNN`. Recovery: `lvremove -f /dev/pve/vm-NNNN-cloudinit /dev/pve/vm-NNNN-disk-0` (and matching LVs). If phantom config exists, prefer `qm destroy NNNN --purge`. Class: state surviving outside orchestrator view.

### Tailscale

- **Tailscale auth keys cap at 90 days** — API hard-clamps `expiry` to 7776000s. Closest to "indefinite": `reusable = true` + `recreate_if_invalid = "always"` + `expiry = 7776000`. Set `lifecycle { ignore_changes = [expiry] }` on provider <0.29.0 (we're on 0.28.0) to avoid plan-flap. **Rebuilding a Tailscale-joined LXC 90+ days after initial mint requires `terraform apply` on the tailscale module BEFORE re-running Ansible** — otherwise Vault holds an expired key and `tailscale up` fails.
- **Tailscale `tailnet_key.description` is alphanumeric + spaces + identifier-internal hyphens only.** Rejects parens, em-dashes, slashes — generic error, bisect-the-description to debug. Safe template: `"<hostname> <tag> managed by terraform"`.
- **Tailscale subnet routers + exit nodes need `net.ipv4.ip_forward=1` AND `net.ipv6.conf.all.forwarding=1`.** `tailscale up` succeeds without; control plane's per-route-family relay check flags "cannot relay traffic." `--advertise-exit-node` implicitly advertises both `0.0.0.0/0` AND `::/0` → triggers v6 check regardless of LAN v6. v6 sysctl is kernel-state-declarative — flag set is sufficient even with link-local-only v6. Persisted via `/etc/sysctl.d/99-tailscale.conf` in the tailscale role.
- **Tailscale DSM 7 package upgrades wipe `configure-host` TUN-permissions state.** Boot-up Task Scheduler task re-applies on next reboot — but package auto-update without reboot silently breaks subnet-router/exit-node. Diagnostic: tailnet admin shows "cannot relay traffic"; `tailscale netcheck` no relay. **Recovery:** `/var/packages/Tailscale/target/bin/tailscale configure-host; synosystemctl restart pkgctl-Tailscale.service` as root. Consider disabling auto-update in DSM Package Center.

### SSH / system

- **Hostkey files can be left zero-byte after hard crash mid-write.** ext4 journal restores inode metadata, not page-cache data. sshd serves from in-memory hostkeys until next restart → `sshd: no hostkeys available -- exiting`. Diagnostic: `ls -la /etc/ssh/ssh_host_*_key` (zero-byte, old mtime); cross-reference `find /etc /var -size 0 -newermt '<window>'`. **Recovery:** `rm` empties first (`ssh-keygen -A` skips existing), then `ssh-keygen -A`, then `systemctl start sshd`. Then `ssh-keygen -R <host>` + `-R <ip>` on control node + operator workstations. Defense pending: baseline asserts hostkey files non-empty.
- **`last reboot` showing multiple "still running" entries = hard crashes.** Only current boot can actually be running — others are boot-without-shutdown records. Useful first-pass diagnostic for "broken since some time ago." Pair with `find -newermt` to localize the crash window. wtmp rolls off via logrotate.

### Shell / tooling

- **fish heredocs `<<EOF` don't work.** Use pipes or temp files. (See feedback memory `feedback_fish_heredocs.md`.)
- **fish command-substitution collapses newlines when echoed via variable.** `set -l var (cmd)` captures list-of-lines; `echo $var | grep` joins with spaces. Preserve structure: pipe directly (`cmd | grep`), or iterate the list (`for line in $var`).
- **`kubectl exec` warning lines pollute scripted parsing.** Multi-container pods print `Defaulted container "X" out of: ...` to stdout. Always pin with `-c <container>` for scripts.

### SFTPGo / Factorio

- **SFTPGo sqlite `data_provider_name` must be absolute** (`/var/lib/sftpgo/sftpgo.db`). Unit's `WorkingDirectory=/etc/sftpgo` makes relative paths FHS-wrong. Create the var-lib dir with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in role.** Background timer races ahead of `initial-install.yml` → `factorio --create` hits `.lock` from already-running service. Pattern: timer `enabled` only in `reconcile.yml`; start explicitly at end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract.** Factorio writes `.lock` at startup; tarball ownership doesn't match. The reconcile script does this — manual installs need it too.

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns. K8s-specific concepts are the knowledge gap. Deep K3s/K8s experimentation is intended for the future jotunheim ("can implode") cluster — asgard is built carefully, not used as a learning sandbox.