# Homelab — Claude Code context
*Loaded every session, so it carries rules and pointers only. Narrative, status and history live in [`docs/homelab-design.md`](docs/homelab-design.md) (index → `architecture/`, `services/`, `operations/`, `incidents/`, `procedures/`, `known-issues/`).*

## What this is

A ground-up homelab on 3 physical nodes (Urd / Verd / Skuld) plus a Synology NAS (Munin). Goals: reliable services for friends/family, a K8s learning environment, a senior/principal-level infrastructure portfolio. The owner is a senior infra engineer (10+ years Ansible); **Kubernetes is the learning gap**, everything else is well known. Explain the *why* behind K8s design choices, not just manifests. Deep K8s experiments go on the ephemeral DigitalOcean burst cluster (`scripts/burst/`), never asgard: asgard is built carefully, not used as a sandbox.

## Hard rules (read these first)

1. **Never put a credential literal in a tool call or chat** — resolve it inside the shell process (see [Secrets in commands](#secrets-in-commands)).
2. **`main` is protected: land changes through a branch + PR**, never push or ff-merge `main` (see [Mutating operations](#mutating-operations)). Merging to `main` IS the K8s deploy.
3. **No `kubectl apply`/`kubectl edit` against production** — Flux reconciles. Never replace K3s addon files (Calico) by hand.
4. **`terraform apply` only from the main checkout**; **one `ansible-playbook` at a time** across all agents.
5. **Pre-flight before proposing new work** (below), and **post-flight docs before calling it done**.
6. The repo is **public**: no secrets, ever; every push is secret-scanned.

## Architectural invariants — never propose violating these

Rationale + dates are in [`docs/operations/decisions.md`](docs/operations/decisions.md) (quoted titles below are row names).

### Orchestration
- **K3s only** (no Swarm, no plain k8s). **One cluster: asgard.** Jotunheim (Phase 7) was dropped 2026-10-05 for lack of RAM; don't propose rebuilding it unless hardware is added. VLANs 30/31, VMIDs 3001–3999 and the Jotunheim names stay reserved.
- **GitOps: Flux CD** (no ArgoCD). Structure: per-component config Kustomizations `<component>-config/` with `dependsOn: infrastructure`, because CRD-dependent resources can't share a Kustomization with the chart that installs the CRD. Existing dirs: `infrastructure-config` (ESO), `metallb-config`, `vault-config`, `synology-csi-config`, `cert-manager-config`, `gateway-config`, `vpa-config`.
- **Calico is NOT Flux-managed.** Tigera operator + Installation are K3s **addon files on the init node**, laid down by `roles/k3s/tasks/calico.yml` (only when the node is not yet healthy) and moved on a live cluster ONLY by `ansible/playbooks/calico-upgrade.yml` (one minor per run, datastore export + per-file CRD-prune guard). **K3s prunes per addon file** — see [`docs/incidents/2026-10-01-calico-datastore-prune.md`](docs/incidents/2026-10-01-calico-datastore-prune.md). Desired version = `calico_version` in `roles/k3s/defaults/main.yml`; `playbooks/platform-version-drift.yml` reports drift.

### Identity / DNS / network
- **Identity: Authentik** (OIDC for web apps, LDAP for SSH via SSSD; local break-glass admins). No Authelia.
- **IPAM/DCIM: NetBox** in asgard, internal-only at `netbox.niflheim.xiiisins.com`. Git is the IaC spec; NetBox is the queryable view. **Every new LXC/VM Terraform resource MUST get a matching `netbox_virtual_machine` + `netbox_interface` + `netbox_ip_address` in `terraform/netbox/vms.tf` locals** (physical devices → `devices.tf`). The provider authenticates with the admin token from 1P "Asgard - NetBox - admin API token" (no dedicated TF user; provider incompatibilities in [`netbox.md`](docs/known-issues/netbox.md)). Provider is pinned (e-breuninger/netbox v5.3.0).
- **DNS: AdGuard Home, NOT Pi-hole.** Three LXCs (Saga/Mimir/Kvasir), keepalived VIP `10.0.10.200`.
- **AGH rewrites are Terraform-managed, never hand-edited in the UI.** Add a record to `locals.rewrites` in `terraform/adguard/rewrites.tf` and apply. The provider writes to Saga (`10.0.11.201`); adguardhome-sync fans out to Mimir/Kvasir. Auth via env from 1P "Adguard - admin" (`homelab-env`). End-to-end smoketest: `curl https://smoketest.niflheim.xiiisins.com/anything` → 200 "smoketest ok" (DNS rewrite + Traefik + backend). ("AGH rewrites — Terraform managed…")
- **DNS zones, three-zone scheme:** `xiiisins.com` (apex, external, Cloudflare) / `midgard.xiiisins.com` (internal alias of publicly reachable services) / `niflheim.xiiisins.com` (internal-only). Each gets its own wildcard cert via cert-manager DNS-01 with one zone-scoped Cloudflare token (`secret/k8s/cert-manager/cloudflare`).
- **MetalLB: L2 mode.** Workers are multi-homed (eth0 VLAN 21 / eth1 VLAN 20); the L2Advertisement `nodeSelectors` exclude CPs (no eth1). Four landmine fixes live in IaC and must never be "hardened" away: [`networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md).
- **Internet exposure: KPN Experia Box → UCG-Ultra DMZ.** UCG is the sole firewall boundary (`Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` last); port-forwards on UCG only. KPN is never in IaC — record changes in docs or they don't exist.

### Storage / data
- **Storage is tiered by access pattern** (forced by the DS223J's hard ~10 DSM-wide iSCSI LUN cap; [`storage-iscsi-synology.md`](docs/known-issues/storage-iscsi-synology.md), [`procedures/synology-storage-redesign.md`](docs/procedures/synology-storage-redesign.md)):
  - **iSCSI** (Synology CSI, class `synology-csi-iscsi-retain-vol2`): block-critical single-instance mmap/fsync DBs only. One LUN per PVC; one synology-csi per cluster; not democratic-csi. Vestigial target `…munin.k3s-core.f954439fc46` is NOT in use.
  - **local-path** (per-worker 50G `/data` xfs): app-replicated/quorum state (Vault Raft) + mmap-safe single-instance.
  - **NFS** (`csi-driver-nfs`, class `nfs-client`, Munin share): large/append file-class volumes.
  - **emptyDir**: cache-class.
- **VM disks: local LVM-thin** (faster than NFS at 1 GbE). **PostgreSQL only, no Galera**; PG backend is local LVM-thin, never NFS (fsync/WAL latency).
- **PG HA: Patroni.** etcd DCS lives on the HAProxy trio (Hlin/Eir/Snotra), NOT the PG nodes. No pgbouncer. Single HAProxy VIP `10.0.10.210` for all consumers; `balance first` + Patroni `/master` health-check routes deterministically to the leader; keepalived VRRP on eth1 (VLAN 10). ("PG HA management — Patroni", "Patroni DCS placement", "pgbouncer in the connection chain", "PG consumer connection model")

### Services / placement
- **Jellyfin: privileged LXC on Urd** (QuickSync `/dev/dri`), not in K3s.
- **Monitoring: Zabbix LXC (Hugin, outside K3s for failure-domain independence) + VictoriaMetrics/Logs in asgard.** **No Grafana** (vmui + the VictoriaLogs UI suffice). Log shipping is **vlagent** only: DaemonSet via the `victoria-logs-collector` chart on K3s, systemd binary via an Ansible role on LXCs/VMs; Vector/Fluent Bit deliberately not used. Off-cluster shippers reach VL through an HTTPRoute on the niflheim Gateway. vm-operator is the Phase 8b target.
- **Ansible scheduling: Semaphore** in asgard K3s (over AWX).
- **PBS: privileged LXC 1101 on Urd.** Never put it back on Skuld (Skuld hard-freezes). The Munin NFS datastore is mounted **inside** the LXC (`features: mount=nfs` + `/etc/fstab`), not bind-mounted from the host; rootfs on `local-lvm`, moved with `pct migrate --restart`.
- **Factorio LXC is the template for operator-managed services:** the operator gets SFTP only (SFTPGo) and edits JSON control files; a root-owned reconcile timer converges state. [`services/factorio.md`](docs/services/factorio.md).

### LXC / infra
- **Two Terraform modules for LXCs.** `terraform/proxmox/asgard-lxcs/` (API-token auth) for normal LXCs; `terraform/proxmox/asgard-lxcs-root/` (root@pam ticket auth, needs `PROXMOX_VE_PASSWORD`) only for LXCs whose create-time config needs ticket auth (`device_passthrough`, future `fuse`/`keyctl`). Don't add root-needing LXCs to the main module.
- **New LXC:** append to the right module's `lxcs.tf` → `terraform apply` → add the NetBox declarations (above) → add to `inventory/hosts.yml` → write role + playbook.
- **LXC bootstrap flow:** Day 1 `terraform apply` → `ansible-playbook … -e 'ansible_user=root' --tags baseline` → full play as `ansible` (hardening ends by locking root SSH: `AllowUsers ansible recovery`). Day N: just the full play. Recovery via the `recovery` user (key in 1P).
- **Repo is PUBLIC since 2026-06-10**, one-way for git history. `ansible-vault` files (`group_vars/all/vault.yml`) are downloadable, so their security rests entirely on the 32-char passphrase. Topology, RFC1918 IPs, Vault paths and 1P item UUIDs are accepted public recon surface (identifiers, never values). SealedSecrets are public-safe by design.

### Secrets — three stores, one rule
*Homelab human lookup → Vault UI (1P offline mirror). Bootstrap + non-homelab human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

- **HashiCorp Vault** (asgard, 3-node Raft HA, AWS KMS auto-unseal, local-path storage): K8s workload secrets via ESO, Ansible lookups via AppRole, human UI at `vault.niflheim.xiiisins.com` behind Authentik OIDC. The listener serves TLS from the cert-manager internal CA (`homelab-internal-ca`, distributed by trust-manager; no plaintext endpoint since 2026-06-20; [procedure](docs/procedures/vault-tls-migration.md)). Traefik re-encrypts to Vault via `BackendTLSPolicy`; ESO trusts the CA via `caProvider`. Config in `terraform/vault/`; **SecretIDs never in Terraform state.**
- **1Password "Homelab" vault:** (1) bootstrap-only creds that must survive Vault being down (root token, KMS unseal token, SSH recovery key, sealed-secrets keypair backup, MacBook AppRole creds); (2) **offline mirror of every Vault-stored homelab secret** (manual discipline, audited; `scripts/secrets/vault-1p-mirror`, [procedure](docs/procedures/secret-mirroring.md)); (3) non-homelab credentials (Proxmox root, Synology, UCG, KPN, personal) live in 1P **outside** the Homelab vault.
- **Ansible Vault** (`group_vars/all/vault.yml`): only what is needed BEFORE Vault is reachable (`k3s_token`, RHEL keys, SSH pubkeys, KMS re-seal copy).
- **Vault paths:** `secret/<consumer-domain>/…` for machine consumers (`k8s/` for K8s workloads, `ansible/` for Ansible-on-LXCs), independent of which TF module mints the secret (the minter writes to the consumer's path). Human-only secrets that never reach a machine (e.g. the Munin Tailscale authkey) stay 1P-only; everything else is minted into Vault by TF and mirrored to 1P.
- Scope rule: things that exist *because the homelab exists* go in the Homelab vault or Vault; personal credentials and infrastructure *under* it live in 1P outside the Homelab vault.

Detail + AppRole bootstrap + control-node tooling: [`docs/architecture/identity-secrets.md`](docs/architecture/identity-secrets.md).

---

## Process

### Pre-flight — before proposing new work
When asked "let's deploy X" / "what's next?" / "plan Y":
1. **Search `docs/` for X** — design, a prior decision ([`decisions.md`](docs/operations/decisions.md)), constraints.
2. **Scan [`open-questions.md`](docs/operations/open-questions.md) for prerequisites**: does X *depend on* an unchecked task, *interact with* one (would X make a latent issue fire), or make a deferred task *urgent*?
3. **Read the matching [`docs/known-issues/`](docs/known-issues/) file(s)** — for X *and* for the systems X depends on (storage class, secret store, networking, DNS, consuming-workload pattern). Open more than one when X has dependencies.
4. **Treat pending tasks as prerequisites, not backlog**; propose the sequence with prerequisites first (Phase 0 / 4a) and say *why* each is a prerequisite.

Output is "I checked these; here's what I found", not a list of clarifying questions. If the owner says "skip the pre-flight", skip it. Flag only real prerequisites, not orthogonal items. (Origin: the 2026-05-17 Authentik deploy hit the un-closed CP-taint task — [retro](docs/incidents/2026-05-17-evening-authentik-redis.md).)

### Post-flight — after work lands
1. **Update docs** — each piece has one home:
   - [`build-sequence.md`](docs/operations/build-sequence.md): tick the phase, add emergent sub-phases (one concise row per phase).
   - [`decisions.md`](docs/operations/decisions.md): rows for new architectural decisions.
   - [`incidents/`](docs/incidents/): non-trivial work (several findings, surprises, recovery) → `YYYY-MM-DD-<slug>.md` + a row in `incidents/README.md`.
   - [`open-questions.md`](docs/operations/open-questions.md): close done items, add new ones.
   - [`known-issues/`](docs/known-issues/): new gotchas (rule, Why, symptom/diagnostic, recovery) in the matching subject file; a new subject needs a new file + a row in `known-issues/README.md`. **Gotcha text never goes in this file.**
   - This file: update the status line below and the invariants/reference only if something moved or was resized. Narrative never goes here.
   - [`architecture/`](docs/architecture/), [`services/`](docs/services/), code-adjacent READMEs: update if scope shifted.
2. **Cross-reference**: a gotcha stemming from a decision links to the `decisions.md` row and vice versa (CI checks relative links: `.github/scripts/ci-doc-links.py`).
3. **Commit** with a conventional-commit subject (reference the phase) and no attribution trailers; docs and code in separate commits where practical.
4. **Name what's next** by applying pre-flight to the next step.

Mid-phase doc updates are only for decision-row changes, pending tasks needing explicit tracking, or reality diverging from the plan. Gotchas, side findings and cosmetics batch to post-flight (one consolidated doc commit at phase close).

### Secrets in commands
Credentials (passwords, tokens, API keys, AppRole SecretIDs, root tokens) must never appear literally in a Bash command, tool input/output, or chat. Resolve them inside the shell process:

```bash
# Right: the value lives only in that command's env
ADGUARD_PASSWORD="$(op read 'op://Homelab 2.0/Adguard - admin/password')" terraform apply
# Wrong: the literal lands in the transcript
ADGUARD_PASSWORD='hunter2' terraform apply
```

- **Your own commands: use the Vault-backed shim**, not 1P — you run non-interactively (incl. `claude remote-control`) where `op` can't prompt. Source the shim first (it is not on PATH), then chain the command so it inherits the exported IaC vars:
  ```bash
  source "$(git rev-parse --show-toplevel)/.config/scripts/homelab.sh" \
    && vault-homelab-env >/dev/null && terraform apply
  ```
  Refresh needs `vault`+`jq` on PATH (prefix `PATH="/opt/homebrew/bin:$PATH"` on the Mac). Cache `~/.cache/homelab/vault-env.{sh,fish}`, 3 h TTL. Internals, field set and the Frigg-vs-MacBook distinction: [`identity-secrets.md`](docs/architecture/identity-secrets.md) ("Vault-backed shim").
- **Warm-cache fallback** when the shim is cold or erroring: `. ~/.cache/homelab/env.sh` (operator-warmed 1P cache, plain exports incl. `KUBECONFIG`, `VAULT_*`, `ANSIBLE_VAULT_PASSWORD_FILE`, `ANSIBLE_PRIVATE_KEY_FILE`). Source it, never `cat` it. Try this before hand-setting individual vars.
- **Operator-facing instructions** recommend the 1P-backed `homelab-env` shim.
- Vault: `$(vault kv get -field=<f> secret/<path>)`. Ansible Vault: `--vault-password-file`, or `ansible-vault view | grep` piped into the consumer.
- Before any Bash call needing a credential, ask "will the literal appear in the tool input?"; same check before pasting an invocation into chat. If one leaked, tell the owner and recommend rotation; **don't auto-rotate**.
- Why: transcripts persist and get shared, and the repo is public.

### Persistence validation
After any change that must survive reboot (network config, sysctl, systemd units, kernel/modules, OS updates), **reboot the affected node before reloading workloads or declaring done**. A runtime fix (`ip link set`, `sysctl -w`) can mask a broken on-disk file that only bites weeks later.

### When the owner pushes back
Acknowledge directly, name the specific pattern that was missed, say how it will be caught next time. No defensiveness, no over-apology, no "I'll do better". If the lens generalizes, add it to this file.

### Stale reads
Re-read a file before editing it; the owner edits between turns. If a patch or edit doesn't match what you expect, `git fetch`/`git log` to check for upstream changes, or ask for a targeted `grep -nA5 '<distinctive line>' <file>` — never regenerate against assumed state.

### Phase structure
`docs/operations/build-sequence.md` is a route-to-done, not a runbook. Numbering: L1 phase `6` → L2 letter `6a` → L3 number `6d1`; deeper than L3 becomes checkboxes. Sub-decompose only for genuinely distinct work units (own state/tools/retry granularity) or an ordering/scope choice worth pinning; not because work touches several files. Pre-rule deep structures stay as history; the underscore form `8j3_5.8.3` is for multi-week sequences only.

| Content | Home |
|---|---|
| What something is and why | `docs/architecture/`, `docs/services/` |
| Step-by-step operations (composable) | `docs/procedures/` |
| Incident retrospectives | `docs/incidents/` |
| Gotchas | `docs/known-issues/` (one file per subject) |
| How a role/module works and is used (brief) | its own `README.md` |
| Process rules, invariants, gotcha index | this file |

### Parallel agents
Fan out `Agent` calls in **one message** for independent work: per-source research, repo-wide investigation, read-only state pulls across hosts, spec-vs-live reconciliation. Don't when the second task needs the first's output, the work mutates the same external system, or merging reports costs more than it saves.
- **Mutating** agents get `isolation: "worktree"` and a NON-overlapping slice; worktrees isolate git, not shared systems (same TF module / namespace / Vault path still race, so sequence those).
- **Brief each agent self-contained** (it can't see this chat): context, do / do-NOT, word budget, return format. Mutating agents also get: branch `feat/<slug>`, conventional commits with **no attribution trailers**, and "do NOT push or merge; return the branch."
- Never merge sub-agent output without reviewing its diff.

### Mutating operations
- **Branch + PR** ([`procedures/ci.md`](docs/procedures/ci.md)): work on `feat/<descriptive-slug>` · `doc/…` · `fix/…` · `chore/…` branches (concise, descriptive; never `claude/<random>`, even if the session or harness suggests one: rename it) with conventional-commit subjects and **no attribution trailers** (no `Co-Authored-By`, no `Claude-Session`). `main` requires the `CI gate` check and rejects direct pushes (admin bypass = break-glass only). Auto-merge is fine for docs and routine bumps; `terraform/`, `ansible/`, `k8s/` PRs get a diff review first.
- **When clean AND tested, push the branch and open/update its PR**; the operator merges. If untested (UI without a browser test, OS config without a reboot test), leave the branch and say why. Don't open a PR the owner didn't ask for unless the session workflow calls for it.
- **`terraform apply`: main checkout only** (plan and HCL edits from worktrees are fine).
- **`kubectl apply` never**; use `flux reconcile …` to nudge. Manifests land via git.
- **`ansible-playbook`: one at a time across all agents** (SSH MaxAuthTries, package locks, handler restarts). Ask first if another agent may be mid-playbook.
- **Chart / platform upgrades: use the `chart-bump` agent** ([`.claude/agents/chart-bump.md`](.claude/agents/chart-bump.md), helpers in `.claude/scripts/chart-bump/`). K3s minors go through [`k3s-upgrade.yml`](ansible/playbooks/k3s-upgrade.yml) ([procedure](docs/procedures/k3s-upgrade.md)); wave status in [`chart-bumps-2026-09.md`](docs/operations/chart-bumps-2026-09.md).

---

## Mechanisms (runtime quick reference)

Detail: [`docs/services/asgard-k3s.md`](docs/services/asgard-k3s.md), [`k3s-lifecycle.md`](docs/known-issues/k3s-lifecycle.md), [`networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md).

- **K3s install** is fully IaC via the Ansible `k3s` role. Pin `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.36.4+k3s1`); on a healthy cluster it is applied only by `playbooks/k3s-upgrade.yml` (one minor per run), so a bump alone does nothing. `detect-state.yml` sets `k3s_already_healthy`, making install/calico skip on re-run (avoids duplicate-join); `config.yml` always runs. Init node defaults to `gondul` (`--cluster-init`) → CPs → workers last. **Rebuilding the init node:** `-e k3s_init_node=hlokk` (any healthy CP) and `kubectl delete node <name>` from a survivor first.
- **VM specs:** `locals.{control_planes,workers}` in `terraform/proxmox/asgard-k3s/main.tf` is authoritative. CPs Göndul/Hlökk/Sigrún on Urd/Verd/Skuld: 2 vCPU / 4 GB / 20 GB, one NIC on VLAN 21, tainted `NoSchedule`, identical by rule (failover symmetry). Workers Einherjar-urd/verd/skuld: 2 vCPU / 16 GB / 30 GB `scsi0` + 50 GB `scsi1` xfs `/data`, eth0 VLAN 21 / eth1 VLAN 20.
- ⚠️ **Workers are multi-homed**; the four landmine fixes in `roles/k3s/tasks/network.yml` (Calico CIDR pin `10.0.21.0/24`, `rp_filter=2`, `route_localnet=1`, VLAN 20 policy routing) must never be hardened away.

## Current build status (at a glance)
*Narrative: [`build-sequence.md`](docs/operations/build-sequence.md). Open items: [`open-questions.md`](docs/operations/open-questions.md). Update this list on phase changes only.*

- ✅ **Foundation:** UCG-Ultra, KPN DMZ, Synology (Munin), Proxmox `niflheim` (PVE 9.x), PBS (LXC 1101 on Urd), three identical MSI Cubi nodes.
- ✅ **Asgard K3s core:** cluster (teardown+rebuild validated), Sealed Secrets, Synology CSI, Vault, ESO, MetalLB, tigera-operator, CP taint.
- ✅ **Edge + services:** Traefik/Gateway API/cert-manager, Cloudflared, Tailscale, AdGuard IaC, Factorio, PG HA, Teamspeak, Authentik, NetBox (+ TF→NetBox), Observability (8a), Zabbix (8c), Hermod, Semaphore, Outline + Garage, Startpage, MicroBin, Immich.
- ✅ **1.0 stabilization (S1–S7)** complete 2026-05-31 ([plan](docs/operations/1.0-stabilization.md)).
- ✅ **Phase 6** Vault OIDC, Frigg control node (HA VM 2900, Vault-backed shim, `claude remote-control`, self-healing `frigg-reauth-listener`), Vault TLS. The fleet `ansible_niflheim` key lives only in a memory-only ssh-agent on Frigg ([`frigg-control-node.md`](docs/known-issues/frigg-control-node.md)).
- ✖ **Phase 7 Jotunheim** dropped 2026-10-05 (capacity); Phase 9's Vault Agent / VSO pilot moves to the burst cluster, then asgard app by app.
- 🔲 **Pending:** Jellyfin LXC (plan [`5h-jellyfin.md`](docs/operations/5h-jellyfin.md)), Phase 8b vm-operator migration.
- 🟡 **Phase 10i pod rightsizing** ([plan](docs/operations/10i-rightsizing.md)): VPA recommend-only live; every Flux-managed pod sized from 30 days of VictoriaMetrics; workers reserve 2 GiB for OS+K3s; Calico stays without requests by decision.
- 🟡 **Phase 10 AIOps & self-healing** ([roadmap](docs/operations/aiops-roadmap.md)): 10a–10e live; 10f autonomous T1 healing deployed with a 14-day canary soak running (started 2026-10-03, read-out ~2026-10-17); 10g stage A (approval-gated canary rebuild) proven; 10h live (forecasting, PR author, drift/incident drafts, k8s burst-test); 10i above. Components: Gná (n8n LXC 1121), Toolbelt API on Frigg, Ratatoskr (Discord bot LXC 1122), canary pool (LXCs 1190–1192), DO offsite `do1` + burst substrate. **`n8n` now means the AIOps agent on Gná** (asgard-K3s n8n removed 2026-10-03). Procedures: `docs/procedures/aiops-*.md`.

## Known gotchas

Gotchas live per subject in [`docs/known-issues/`](docs/known-issues/) (index with "when to read" hints: [`README.md`](docs/known-issues/README.md)). **Read the matching file before working on its subject** (pre-flight step 3). Adding one: edit the matching file; never paste gotcha text here. A new subject = new file + a row in the known-issues README.

Subject files: networking-multi-homed-workers · storage-iscsi-synology · garage · k3s-lifecycle · vault · flux-helm-kustomize · k8s-scheduling · traefik-gateway-api · authentik · cloudflare · digitalocean · dns-adguard · postgres · haproxy-keepalived · netbox · frigg-control-node · ansible-roles · lxc-proxmox · tailscale · ssh-system · shell-tooling · terraform-state · observability · sftpgo-factorio · zabbix · caddy · semaphore · n8n-aiops · outline · microbin · ci-github-actions.

## Reference (quick facts that cause mistakes)

Full tables: [`architecture/hardware.md`](docs/architecture/hardware.md) (nodes, storage tiers, **naming convention**), [`architecture/network.md`](docs/architecture/network.md) (VLANs, per-LXC IPs, DNS, firewall).

- **Hardware:** Urd / Verd / Skuld are identical MSI Cubi (i3-1215u, 32 GB, 1 TB NVMe on Urd/Verd, 512 GB on Skuld); Munin = Synology DS223J, 3.5 TB RAID1. All 1 GbE, no 2.5 GbE planned. etcd fsync: Verd ≈ Skuld > Urd (Urd's DRAM-less NVMe), all within tolerance. Urd long-term hosts the Jellyfin LXC. **Skuld hard-freezes** (see PBS rule).
- **MGMT subnet is `10.0.254.0/24`**, NOT `10.0.1.0/24` (an earlier draft nearly "fixed" a correct iSCSI portal). UCG `10.0.254.1`, Urd/Verd/Skuld `.11/.12/.13`, Munin `.20`.
- **VLANs:** 1 `10.0.254.0/24` MGMT · 10 `10.0.10.0/24` asgard VIPs · 11 `10.0.11.0/24` asgard LXCs · 20 `10.0.20.0/24` K3s MetalLB · 21 `10.0.21.0/24` K3s nodes · 30/31 Jotunheim (reserved) · 60 clients · 100 storage · 222 untrusted.
- **Key IPs:** AdGuard VIP `10.0.10.200` (Saga/Mimir/Kvasir `10.0.11.201–203`) · PG HAProxy VIP `10.0.10.210` · PBS `10.0.11.20` · Hugin (Zabbix) `.21` · Tailscale LXCs `.213–.215` · Factorio `.220` · Gná `.221` · Ratatoskr `.222` · PG Fulla/Vör/Idunn `.230–.232` · HAProxy+etcd Hlin/Eir/Snotra `.233–.235` · CPs `10.0.21.11–.13` · workers eth0 `10.0.21.21–.23` / eth1 `10.0.20.201–.203` · Traefik VIP `10.0.20.10` · MetalLB pool `10.0.20.11–.99`.
- **Cluster CIDRs:** pod `10.42.0.0/16` (`k3s_pod_cidr`, K3s `cluster-cidr` AND Calico ipPool), service `10.43.0.0/16`.
- **VMID/CTID ranges:** `1101–1199` asgard LXCs (1101–1109 backup+mon, 1110–1119 net, 1120–1129 services, 1130–1139 DB+HAProxy; canaries 1190–1192) · `2001–2999` asgard K3s VMs · `3001–3999` Jotunheim (reserved) · `9900–9999` DigitalOcean offsite (NetBox cross-reference only; `do1` = 9900) · `10001+` templates.
- **Naming:** Norse mythology throughout; the primary defines the theme, replicas expand within it. Clusters: Proxmox `niflheim`; nodes = the Norns; NAS = Munin; CPs = Valkyries; AdGuard Saga/Mimir/Kvasir; PG = Frigg's handmaidens (Fulla/Vör/Idunn; HAProxy/etcd Hlin/Eir/Snotra); Zabbix = Hugin (pairs with Munin); AIOps agent = Gná; Discord bot = Ratatoskr. New names: see `hardware.md`.
- **Repo map:** `terraform/` (proxmox/{asgard-k3s,asgard-lxcs,asgard-lxcs-root,asgard-vms} · vault · cloudflare · authentik · tailscale · netbox · adguard · garage · semaphore · github · aws · digitalocean) · `ansible/` (inventory/ with NetBox dynamic inventory, playbooks/, roles/) · `k8s/asgard/` (flux-system/, infrastructure/, `<component>-config/`, apps/; `k8s/jotunheim/` is shelved) · `aiops/` (Phase 10 schemas, registry, toolbelt, bot; see `aiops/README.md`) · `docs/` · `.github/workflows/` · `docker/` · `.claude/` (agents + scripts). Per-module detail: [`docs/services/asgard-k3s.md`](docs/services/asgard-k3s.md).
