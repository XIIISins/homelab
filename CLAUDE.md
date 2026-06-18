# Homelab — Claude Code context
*Operational runtime context. Full design + history in [`docs/homelab-design.md`](docs/homelab-design.md) (index → `architecture/`, `services/`, `operations/`, `incidents/`, `procedures/`).*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes (Urd / Verd / Skuld). Goals: reliable services for friends/family, K8s learning environment, portfolio at senior/principal infrastructure level. Owner: senior infra (10+ years Ansible, career in IT/sysadmin/platform). Kubernetes is the primary learning gap; everything else is well-known.

**Where to look:**
- [`docs/homelab-design.md`](docs/homelab-design.md) — top-level index
- [`docs/architecture/`](docs/architecture/) — hardware, network, identity-secrets, IaC layering
- [`docs/services/`](docs/services/) — per-service deep dives (asgard-k3s, jotunheim-k3s, LXCs, Factorio, Postgres, Synology)
- [`docs/operations/`](docs/operations/) — build sequence, decisions log, open questions, [1.0 stabilization plan](docs/operations/1.0-stabilization.md)
- [`docs/incidents/`](docs/incidents/) — per-incident retrospectives (date-indexed in README)
- [`docs/procedures/`](docs/procedures/) — operational runbooks (teardown-rebuild, etc.)

---

## Architectural invariants — never propose violating these

Each rule cross-refs [`docs/operations/decisions.md`](docs/operations/decisions.md) where rationale + date live.

### Orchestration
- **K3s only.** No Docker Swarm, no plain k8s. (decisions: "Orchestrator")
- **Two K3s clusters: asgard + jotunheim.** Separation is failure-domain risk, NOT maturity. Both host production services. Don't suggest merging. (decisions: "Two K3s clusters", "Cluster split criterion")
- **GitOps: Flux CD.** No ArgoCD. No manual `kubectl apply` for production. (decisions: "GitOps")
- **Flux structure: per-component config Kustomizations** named `<component>-config/`, with `dependsOn: infrastructure`. CRD-dependent resources can't live with the chart that installs the CRD; bundling causes shared failure domains. Current `<component>-config` dirs: `infrastructure-config` (ESO), `metallb-config`, `vault-config`, `synology-csi-config`, `cert-manager-config`, `gateway-config`. (decisions: "Flux structure")
- **Calico CNI is NOT Flux-managed.** Installed as K3s addon via Ansible template (`roles/k3s/templates/calico-installation.yaml.j2` → `/var/lib/rancher/k3s/server/manifests/calico-installation.yaml`, applied by `roles/k3s/tasks/calico.yml` only on `k3s_init_node`). Change Calico config via the template + replay; `kubectl edit` gets reverted by the addon controller.

### Identity / DNS / network
- **Identity: Authentik in asgard K3s.** OIDC for web apps, LDAP for SSH via SSSD. Local admin accounts as break-glass. No Authelia.
- **IPAM/DCIM: NetBox in asgard K3s.** Source of truth for devices/VMs/LXCs/IPs/VLANs/prefixes (live since 5i, 2026-05-24). Git remains the IaC spec; NetBox is the queryable view. TF→NetBox standing pattern is live in `terraform/netbox/` (e-breuninger/netbox v5.3.0; pinned, see provider gotchas): **every new LXC/VM TF resource MUST have a matching `netbox_virtual_machine` + `netbox_interface` + `netbox_ip_address` declaration in `terraform/netbox/vms.tf` locals** (and the same for physical devices in `devices.tf` if anything new lands). Provider authenticates via admin token from 1P "Asgard - NetBox - admin API token" (no dedicated `terraform` NetBox user — provider has user/token resource incompatibilities; see [`docs/known-issues/netbox.md`](docs/known-issues/netbox.md)). NetBox is K8s-fronted via Traefik at `netbox.niflheim.xiiisins.com` (internal-only). Authentik OIDC for login; permissions assigned manually in NetBox UI until SOCIAL_AUTH_PIPELINE override lands.
- **DNS: AdGuard Home, NOT Pi-hole.** Three LXCs (Saga/Mimir/Kvasir), keepalived VIP `10.0.10.200`.
- **AGH rewrites: Terraform-managed in `terraform/adguard/` — never hand-edited in the AGH UI.** New internal DNS records land via the `locals.rewrites` map in `terraform/adguard/rewrites.tf` (FQDN → answer) + `terraform apply`. Provider targets Saga origin (`10.0.11.201`); adguardhome-sync fans writes to Mimir + Kvasir on cron. Provider auth via env (`ADGUARD_HOST`/`SCHEME`/`USERNAME`/`PASSWORD`) loaded by `homelab-env` from 1P "Adguard - admin". Smoketest URL: `curl https://smoketest.niflheim.xiiisins.com/anything` → 200 "smoketest ok" confirms DNS rewrite + Traefik routing + backend healthy end-to-end. (decisions: "AGH rewrites — Terraform managed via gmichels/adguard, write-to-origin")
- **DNS zones: three-zone scheme.** `xiiisins.com` (apex, external, Cloudflare-resolved) / `midgard.xiiisins.com` (internal alias for publicly-reachable services, AdGuard) / `niflheim.xiiisins.com` (internal-only, AdGuard). Each zone gets its own wildcard cert via cert-manager DNS-01 against the same zone-scoped Cloudflare token (`secret/k8s/cert-manager/cloudflare`, scope `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`). (decisions: "DNS — three-zone scheme", "External TLS posture")
- **MetalLB: L2 mode.** Workers are multi-homed (eth0 VLAN 21 / eth1 VLAN 20, eth1 IPs `10.0.20.201/202/203` outside the pool). L2Advertisement uses `nodeSelectors` excluding CP nodes (CPs have no eth1). Four landmine fixes required in IaC — see [`docs/known-issues/networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md).
- **Internet exposure: KPN Experia Box → UCG-Ultra DMZ.** UCG is the sole firewall policy boundary. UCG posture: `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). Port-forwards on UCG only. KPN is never in IaC — changes recorded in docs or they don't exist.

### Storage / data
- **Storage: Synology CSI driver (christian-schlichtherle/synology-csi-chart), iSCSI only.** Single StorageClass `synology-csi-iscsi-retain` (default). NFS creates polluting shared folders on Synology. Not democratic-csi. One CSI instance per K3s cluster.
- **iSCSI LUNs are per-PVC.** Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). Vestigial target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` from an abandoned NFS-CSI attempt is NOT in use.
- **VM disks: local LVM-thin.** Faster than NFS at 1 GbE.
- **No Galera. PostgreSQL only.** Zabbix migrated to PostgreSQL.
- **PG backend: local LVM-thin** (not NFS). NFS fsync semantics + WAL latency over 1 GbE are anti-patterns. (decisions: "PG storage backend")
- **PG HA via Patroni** (5g.2 live 2026-05-24). etcd DCS co-located on HAProxy trio (Hlin/Eir/Snotra), NOT on PG nodes. No pgbouncer (revisit triggers documented). Single HAProxy VIP `10.0.10.210` for all consumers — leader-detection via Patroni REST API `/master` health-check (`balance first` + only-leader-returns-200 → fully deterministic routing). VIP floats across the HAProxy trio via keepalived; VRRP on eth1 (VLAN 10). (decisions: "PG HA management — Patroni", "Patroni DCS placement", "pgbouncer in the connection chain", "PG consumer connection model", "HAProxy/etcd LXC dual-NIC", "keepalived election model", "VRID spacing convention")

### Services / placement
- **Jellyfin: privileged LXC on Urd.** Intel QuickSync `/dev/dri` passthrough. Not in K3s.
- **Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs in asgard K3s.** Zabbix stays LXC for monitoring independence (separate failure domain from K3s). VM + VL live in asgard rather than jotunheim — keeps the log-ingest path in-cluster for the workloads producing logs + sidesteps the jotunheim-deploy timing dependency. VictoriaLogs replaces Loki; VictoriaMetrics replaces Prometheus. **No Grafana** — vmui (built into vmsingle) + the native VictoriaLogs UI cover homelab-scale dashboards; revisit only if cross-source dashboards-as-code becomes a real need. Log shipping via **vlagent** (the official VictoriaLogs project shipper) — DaemonSet on K3s nodes via the `victoria-logs-collector` Helm chart (vlagent with `-kubernetesCollector`, native pod-log discovery), plus an Ansible role deploying vlagent as a systemd binary on LXCs/VMs. Vector + Fluent Bit deliberately NOT used — both have file-rotation correctness issues + the 2026 benchmark shows vlagent at 4-10× lower CPU. Off-cluster shippers (LXCs, VMs) reach VL via an HTTPRoute on the niflheim Gateway (cleaner than a MetalLB LoadBalancer + matches the K8s-fronted-FQDN pattern). vm-operator (`VLSingle` / `VMSingle` CRDs) is the Phase 8b refactor target — Helm chart for now.
- **Ansible: AWX in jotunheim K3s.** 30-min scheduled reconciliation. Vault-backed credentials.
- **PBS: privileged LXC on Skuld.** NFS bind-mounted via Proxmox host.

### Secrets — three stores, one rule
*Homelab human lookup → Vault UI (post-Phase 6) with 1Password as offline mirror. Bootstrap + non-homelab human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

- **HashiCorp Vault** (machines at runtime AND primary human accesspoint for homelab secrets, asgard K3s) — K8s workload secrets via ESO, Ansible role lookups via AppRole, **Vault UI behind Authentik OIDC at `vault.niflheim.xiiisins.com` for human lookup (Phase 6)**. 3-node Raft HA, AWS KMS auto-unseal, iSCSI storage. Vault listener is `tls_disable = 1` — deliberate (see [`docs/known-issues/vault.md`](docs/known-issues/vault.md)); Traefik terminates TLS at the niflheim Gateway. Vault config (auth methods, policies, roles, KV) in `terraform/vault/`; SecretIDs NEVER in Terraform state.
- **1Password "Homelab" vault** — three roles: **(1) bootstrap-only creds** that must survive Vault being down (Vault root token, AWS KMS unseal token, SSH recovery key, sealed-secrets master keypair backup, AppRole creds for the MacBook control node). **(2) Offline mirror of every homelab Vault-stored secret** — discipline rule, manually-maintained, periodic audit. **(3) Non-homelab credentials** (Proxmox root, Synology admin, UCG-Ultra, KPN, banking, personal, family-shared) — lives in 1P **outside** the Homelab sub-vault.
- **Ansible Vault** (machines at bootstrap, `group_vars/all/vault.yml`) — narrowly scoped: `k3s_token`, RHEL keys, SSH pubkeys, AWS KMS re-seal copy. Only what's needed BEFORE HashiCorp Vault is reachable.

Vault path convention: `secret/<consumer-domain>/...` for **machine consumers** (`k8s/` for K8s workloads, `ansible/` for Ansible-on-LXCs). Path is independent of which TF module mints the secret — minter writes to consumer's path. Human-only secrets that NEVER reach a machine consumer (Munin Tailscale authkey, etc.) stay 1P-only. Everything else: TF mints to Vault as primary, operator mirrors to 1P as offline backup. (decisions: "Secrets architecture", "Bootstrap-vs-runtime split", "Vault path convention", "Module ownership of Vault KV secrets", "Vault UI as primary human accesspoint for homelab secrets (Phase 6)")

Scope rule: "things that exist *because the homelab exists*" go in the Homelab vault or Vault. Personal credentials and infrastructure *under* the homelab (Proxmox root, Synology admin, UCG-Ultra, KPN) live in 1Password but **outside** the Homelab vault.

Full details + AppRole bootstrap runbook + control-node fish tooling: [`docs/architecture/identity-secrets.md`](docs/architecture/identity-secrets.md).

### LXC / infra
- **LXC management: two Terraform modules.** `terraform/proxmox/asgard-lxcs/` (API-token auth, same tfvars as `asgard-k3s/`) for normal LXCs; `terraform/proxmox/asgard-lxcs-root/` (root@pam ticket auth, needs `PROXMOX_VE_PASSWORD` env) for LXCs whose create-time config touches API endpoints that only accept ticket auth — today just `device_passthrough` (Tailscale `/dev/net/tun`), future: `fuse`, `keyctl`, additional passthroughs. Don't add root-needing LXCs to the main module — the recurring `op read` foot-gun on every apply is exactly why the split exists. Add new LXC: append to the right module's `lxcs.tf` → `terraform apply` → add to `inventory/hosts.yml` → write role + playbook.
- **LXC bootstrap flow.** Day 1: `terraform apply` → `ansible-playbook ... -e 'ansible_user=root' --tags baseline` → full play as `ansible` (hardening locks root SSH out at the end via `AllowUsers ansible recovery`). Day N: just the full play. Recovery via the `recovery` break-glass user (key in 1Password).
- **Factorio LXC pattern: operator self-service via SFTPGo + reconcile loop.** Template for future operator-managed services (game/voice). Operator never gets shell — SFTP into `/factorio/` + edits JSON control files; root-owned Python reconcile script (systemd timer, 30s) converges actual state. Full design: [`docs/services/factorio.md`](docs/services/factorio.md).
- **Repo: PUBLIC GitHub (since 2026-06-10).** Secrets never in Git regardless; SealedSecrets used for bootstrap secrets (public-safe by design — encrypted to the cluster's public key). **Public-repo posture:** every push is secret-scanned before it lands; git history was scanned clean (external scan, 2026-06-10). `ansible-vault`-encrypted files (`group_vars/all/vault.yml`) are now publicly downloadable — their security rests ENTIRELY on the 32-char vault passphrase (offline-brute-forceable; keep it strong, rotate if ever weakened). Internal topology / RFC1918 IPs / Vault paths / 1Password item UUIDs are now public recon surface (identifiers, not values — accepted portfolio tradeoff). Going public is **one-way for git history**: re-privatizing does not un-expose anything already pushed, so the never-commit-secrets discipline is now load-bearing, not best-effort.

---

## Process expectations

Procedural instructions for Claude. Follow on every session before drafting plans or implementations.

### Pre-flight — before proposing new work

When the owner says "let's deploy X" / "what's next?" / "let's plan Y", in order:

1. **Search `docs/` for X.** What does design say? Has a decision been made ([`docs/operations/decisions.md`](docs/operations/decisions.md))? Any constraints?
2. **Scan [`docs/operations/open-questions.md`](docs/operations/open-questions.md) for prereqs of X.** Specifically: does X *depend on* an unchecked task (architecturally — would deploying X be wrong or fragile without it)? Does X *interact* with an unchecked task (would the new workload make a latent issue fire)? Does X *make* a pending task more urgent (was it deferred because nothing exercised the gap — and is X that thing)?
3. **Read the relevant [`docs/known-issues/`](docs/known-issues/) file(s) for X and adjacent systems.** Match subject → file via the index in [`docs/known-issues/README.md`](docs/known-issues/README.md) (mirrored in this file's "Known gotchas" section). Not just "is there a gotcha for X" but "what gotchas hit systems X depends on" (storage class, secret store, networking, DNS, the consuming workload pattern) — so open more than one file when X has dependencies.
4. **Treat pending tasks as prerequisites, not backlog.** When a new workload exercises pending-task debt, pull the task forward. Surface explicitly: "before X, items A, B, C should close because they affect X in ways Y, Z."
5. **Propose the sequence with prerequisites first.** Don't draft Phase 1 of X if Phase 0 should be a pending-task closure. Name prerequisites as Phase 0 (or 4a, etc.) with *why* it's a prerequisite rather than nice-to-have.

Cost: a few minutes of doc-search. Output: "I checked these; here's what I found." Not multi-turn interrogation.

The lens that should have caught the 2026-05-17 evening CP-taint miss (Authentik deploy exercised the un-closed `node-role.kubernetes.io/control-plane:NoSchedule` task — see [`docs/incidents/2026-05-17-evening-authentik-redis.md`](docs/incidents/2026-05-17-evening-authentik-redis.md)). This checklist exists because of that.

### Post-flight — after completing work

When work lands successfully:

1. **Update docs.** Each piece has a specific home:
   - [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md) — tick phase, add sub-phases that emerged
   - [`docs/operations/decisions.md`](docs/operations/decisions.md) — add rows for new architectural decisions
   - [`docs/incidents/`](docs/incidents/) — if work was non-trivial (multiple findings, surprises, recovery steps), add `YYYY-MM-DD-<slug>.md` with findings list + update `incidents/README.md` index
   - [`docs/operations/open-questions.md`](docs/operations/open-questions.md) — mark closed items, add new items surfaced
   - [`docs/known-issues/`](docs/known-issues/) — add new gotcha classes (rule + Why + symptom/diagnostic + recovery commands) to the matching subject file; add a new file + an index row in `docs/known-issues/README.md` if the subject is new. **Gotcha TEXT never goes in `CLAUDE.md`** — only the index pointer.
   - This file (`CLAUDE.md`):
     - "Current build status" — update ✅/🟡/🔲 line (at-a-glance only; narrative goes in build-sequence.md)
     - "Known gotchas" index — add a pointer row only if a brand-new subject file was created
     - Architectural invariants / hardware / repo-structure / reference sections — update if anything moved or got resized
   - [`docs/architecture/`](docs/architecture/), [`docs/services/`](docs/services/), code-adjacent READMEs — update if scope shifted

2. **Choose patch vs full file.** Signal-density vs friction:
   - **<30 lines / <5 hunks** → patch (unified diff, `-p1` paths, run `patch --dry-run -p1` against source before delivery; if it fails, fall through to staleness rule below)
   - **>100 lines or restructuring** → full file (deliver via file mechanism, not inline)
   - **30–100 lines** → ask the owner. Default full file if change spans many sections; patch if contiguous edits in 1-2 sections.

3. **Cross-reference between docs.** A gotcha in `docs/known-issues/` that stems from an architectural decision should link to the `decisions.md` row, and vice versa. Keep both navigable independently.

4. **Suggest the commit message.** Conventional commits, concise subject. Reference phase number if applicable.

5. **Name what's next.** Apply pre-flight to the next step.

### Never echo secrets in tool calls or chat output

Credentials (passwords, tokens, API keys, AppRole SecretIDs, Vault root tokens, anything sensitive) must NEVER appear literally in:

- Bash tool commands (`curl -u 'user:LITERAL'`, `VAR=literalvalue cmd ...`, etc.)
- Chat text shown to the owner (recommended invocations, copy-paste blocks)
- Any tool input or output

**Always fetch dynamically inside the shell process** so the value lives only in that command's env and never lands in the transcript:

```bash
# Right: 1P value resolved inside the shell, never in tool input
ADGUARD_PASSWORD="$(op read 'op://Homelab 2.0/Adguard - admin/password')" \
ADGUARD_USERNAME="$(op read 'op://Homelab 2.0/Adguard - admin/username')" \
terraform apply

# Wrong: literal password lands in the tool call + transcript
ADGUARD_PASSWORD='hunter2' terraform apply
```

For the **operator's** own interactive use, the 1P-backed `homelab-env` shim loads from 1P + caches without ever echoing the values — that's the right path to recommend in *user-facing* instructions (the operator can `op signin`).

**For Claude's OWN machine commands, prefer the Vault-backed shim (`vault-homelab-env`), NOT the 1P one.** Claude runs non-interactively — and increasingly via `claude remote-control` on a control node — where `op` can't prompt for biometric/signin, so the 1P cache (`env.sh`) only works if the operator happened to run `homelab-env` by hand recently. `vault-homelab-env` needs no human: it AppRole-logs-in to Vault with the on-disk secret-zero (`~/.config/ansible/vault-approle.env`) and pulls the same IaC creds from `secret/ansible/frigg/*`. Self-healing one-liner for any multi-var command — re-fetches from Vault when the 3h cache is stale, fast cache-read when warm:

```bash
# Preferred for Claude's multi-var commands — Vault-backed, no 1Password.
# `vault-homelab-env` isn't on PATH; it's defined in the repo shim, so source
# that first. Calling it exports every IaC var into THIS shell → the chained
# command inherits them.
source "$(git rev-parse --show-toplevel)/.config/scripts/homelab.sh" \
  && vault-homelab-env >/dev/null && terraform apply
```

(The refresh path needs `vault` + `jq` on PATH — prefix `PATH="/opt/homebrew/bin:$PATH"` per the "Bash tool calls don't inherit … PATH" gotcha. The known-warm form `. ~/.cache/homelab/vault-env.sh && <cmd>` needs neither, but does NOT self-heal a stale cache.)

`~/.cache/homelab/vault-env.sh` (POSIX) + `vault-env.fish` (fish) are written by `vault-homelab-env` (**3h TTL**, SEPARATE from the 1P `env.{sh,fish}`) and hold the same IaC set: `KUBECONFIG`, `VAULT_ADDR`/`VAULT_TOKEN`, `ADGUARD_*`, `CLOUDFLARE_API_TOKEN`, `AUTHENTIK_*`, `NETBOX_*`, `AWS_*`, `TF_VAR_proxmox_api_token`, `SEMAPHOREUI_*`, the `ANSIBLE_HASHI_VAULT_*` AppRole vars + `ANSIBLE_VAULT_PASSWORD_FILE`. The cached `VAULT_TOKEN` is a ~30 min AppRole token — `vault-homelab-env --refresh` re-mints it. Adding a new IaC field means appending to `__vault_homelab_iac_map` in BOTH shim files (and `control_node_iac_env_fields` in `roles/control-node` if Frigg should get it) + `vault-homelab-env --refresh`. The on-disk secret-zero is re-synced after a `rotate-approle` via `homelab-env` (1P) then `seed-vault-approle`.

> The 1P-backed `. ~/.cache/homelab/env.sh` / `homelab-env` still works at home and stays the operator's interactive path — it's just no longer Claude's default. On **Frigg** the distinction is moot: its `homelab-env`/`env.sh` is *already* Vault-backed (no `op` on the box), so there `. ~/.cache/homelab/env.sh` IS the Vault path.

For Vault: `$(vault kv get -field=<f> secret/<path>)` follows the same pattern. For Ansible Vault: `--vault-password-file` or `ansible-vault view | grep` piped into the consumer, never copy-paste-in-prompt.

**Why:** Transcripts persist (Claude bg-session logs, terminal scrollback, `gh pr view` for any pasted command). Even on a private workstation the discipline matters — and now that the repo is PUBLIC it is non-negotiable — because (a) transcripts get shared during debugging, (b) the owner shouldn't have to mentally redact what they're showing someone, and (c) it normalizes a habit that DOES matter in shared/production contexts.

**How to apply:** Before any Bash tool call needing a credential, ask: "is the literal value about to appear in the tool input?" If yes, restructure to `$(op read ...)` / `$(vault kv get ...)`. Same check before pasting any invocation into chat.

**Recovery if a credential leaked in-session:** flag it to the owner and recommend rotation — don't auto-rotate without an explicit ask, that call is theirs.

### Persistence validation — reboot-test before declaring done

After any change that must survive reboot — physical hardware operations, persistent config edits (`/etc/network/interfaces`, `/etc/sysctl.d/`, systemd units, kernel/module changes), OS updates — reboot the affected node BEFORE re-loading workloads or marking the work complete. Common failure mode: runtime fix worked (`ip link set`, `sysctl -w`) but on-disk file has a typo / wrong path / syntax error — surfaces only at next reboot, often days or weeks later when context is lost. Established 2026-05-23 during Phase 4c Verd refresh.

### When the owner pushes back

If the owner says "X should have happened differently" or "we missed Y" — acknowledge directly, name the specific pattern that was missed, propose how to catch it next time. Don't be defensive, don't over-apologize, don't promise "I'll do better." Name the lens that was missing and add it to this file if it generalizes beyond the immediate case. The above checklists are the result of exactly this kind of feedback.

### Working with stale file snapshots — ask, don't speculate

`/mnt/project/` is a snapshot at session-load. The owner edits between turns. When delivering patches (or making edits that depend on current file shape) and the snapshot disagrees with reality:

**Ask the owner for a targeted grep**, don't speculate or regenerate against assumed state. A few lines of grep output is cheap; regenerating against the wrong baseline wastes a turn and produces a wrong patch.

Concrete asks:
- "Patch failed at hunk N. `grep -nA5 '<distinctive line>' <file>`?"
- "Before I edit, `sed -n 'X,Yp' <file>` to confirm what's there now?"
- "What does `head -100 <file>` look like right now?"

Targeted only. Don't ask for the whole file because Claude can't figure out which 20 lines matter.

### Phase structure & doc separation

Implementation plan in [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md) is a route-to-done, not a runbook. Structural rules:

**Decomposition depth — default 3 levels.**
- L1: phase number (`6`)
- L2: letter (`6a`, `6b`, `6c`, `6d`)
- L3: numeric (`6d1`, `6d2`)
- Beyond L3 → checkboxes inside the L3 step, not deeper numbering.

**Decomposition triggers** — only sub-decompose when one of these is true:
- L2: genuinely distinct work-units with their own state/tools/retry granularity (Terraform VMs vs Ansible provisioning vs Flux deployment vs per-service rollout)
- L3: internal ordering or scope-choice within an L2 step worth pinning in the plan
- Don't sub-decompose because work touches multiple files, crosses tool boundaries, or just "feels big."

**Escape hatch — extremely complex work:** underscore form `8j3_5.8.3` for multi-week sequences needing explicit inter-session state. Default OFF.

**Existing too-deep structures stay as historical record.** Pre-rule structures (e.g. `5e.3.e.iv`) are not retroactively flattened.

**Content separation — where things live.**
- [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md) — concise one-line phase rows with status tick
- [`docs/architecture/`](docs/architecture/), [`docs/services/`](docs/services/) — what something is and why, not how to deploy
- [`docs/procedures/`](docs/procedures/) — step-by-step operational content. Composable (a "deploy a VM" procedure can reference "Terraform deploy" + "Ansible provision")
- [`docs/incidents/`](docs/incidents/) — per-incident retrospectives
- [`docs/known-issues/`](docs/known-issues/) — **canonical home for all gotchas** (migrated out of CLAUDE.md 2026-06-18). One file per subject; index in its `README.md`. Read the relevant file before working on its subject.
- `CLAUDE.md` — process rules + Claude-coding context + architectural invariants + the gotcha **index** (pointers, not text)
- Code-adjacent `README.md` (role/module dir) — how the thing works and how to use it. Brief.

**Mid-phase doc updates** — only for:
- Decision row changes (plan itself shifting)
- Pending tasks surfacing that need explicit tracking before phase close
- Architectural reality diverging from plan assumption

NOT for: implementation gotchas (batch to post-flight), findings not affecting active plan (batch), cosmetic improvements (batch). Phase close gets one consolidated doc commit.

### Boundaries on these checklists

- These are process rules, not rules-about-rules. They tell Claude how to *approach* work; they don't override domain-specific instructions elsewhere.
- If the owner explicitly says "skip the pre-flight, just write the manifest" — skip it. They're the architect, not Claude.
- The pre-flight is a few-minutes-of-doc-search step, not a multi-turn interrogation. Output: "I checked these things; here's what I found" — not a list of clarifying questions.
- Pending tasks: flag as prerequisites when they're prerequisites; not when they're orthogonal. Use judgment.

### Parallel agents — fan out for independent work

The Agent tool can run multiple sub-agents in parallel. Use this aggressively for any work where the sub-tasks are independent:

- **Research** — upstream docs, GitHub issues, release notes, benchmarks. One agent per source class, run concurrently, each capped at a tight word budget. Beats serial fetching by ~Nx the agent count.
- **Investigation** — "find every reference to X across the repo", "audit which manifests use storage class Y", "list all open-questions older than 30 days". Pure read, independent shards.
- **State / reality pulls** — read-only SSH sweeps across multiple hosts (e.g. "what's installed on each AGH node?"), read-only kubectl across namespaces, parallel `qm list` against multiple Proxmox hosts. Fan out per host.
- **Cross-source reconciliation** — agent A reads the IaC spec, agent B reads the live state, then Claude diffs the two reports.
- **Mutating work on disjoint scopes** — e.g. one agent edits `terraform/netbox/`, another edits `k8s/asgard/apps/foo/`, a third writes an Ansible role. **Requires worktree isolation** (see below) — otherwise they race on the working tree and every commit lands on `main`.

**Worktree isolation is the default for any agent that may edit files.** Pass `isolation: "worktree"` on every Agent call where the agent might write — every research/investigation call without writes can skip it. The harness creates a temp git worktree on a fresh branch; the agent commits there; if it made no changes the worktree auto-cleans, otherwise the harness returns the branch + path so the parent agent can merge or discard deliberately. This prevents the "3 agents all committing to `main`" failure mode entirely.

**Disjoint scope is the parent agent's job, not the harness's.** Worktrees isolate git state, NOT shared external systems. Two agents both `terraform apply`ing the same module serialize at the S3 lock (the second errors with "Error acquiring the state lock"); two agents `kubectl apply`ing to the same namespace race on the live cluster regardless of worktree; same for Vault writes, NetBox writes, Cloudflare zone edits. When fanning out mutating work, brief each agent on a non-overlapping slice (different TF module, different K8s namespace, different Ansible role) — and call out the shared-state surfaces in the prompt so the agent knows what NOT to touch.

**When NOT to parallelize:**
- When the second agent's task depends on the first's output (serial-by-design).
- When the work mutates the same external system (same TF module, same K8s namespace, same Vault path) — worktrees don't help; sequence them instead.
- When the cost of context-switching between sub-agent reports exceeds the parallelism gain (small jobs where one agent's full report fits in a single Read).

**Briefing:** each agent gets a self-contained prompt — context, what to do, what NOT to do, a strict word budget, and the format of the expected return. Sub-agents can't see your conversation, so prompt them like a smart colleague who just walked in. Mutating agents also need: the branch name convention (`feat/<slug>`, `fix/<slug>`, etc.), the commit message style (conventional commits, no `Co-Authored-By` per repo norm), and an explicit "do NOT push, do NOT merge to main — return the branch + summary, the parent agent decides."

**Merging back:** when an agent returns with a branch, the parent agent (or operator) reviews the diff and either `git merge --ff-only <branch>` / `git rebase main <branch> && git merge --ff-only` / discards via `git branch -D <branch> && git worktree remove <path>`. Never auto-merge multi-agent output without a diff review — that's the polluting-main failure mode in a different costume.

Pattern: when launching parallel agents, put all the Agent tool calls in a single message. The harness fans them out concurrently; results return in parallel.

### Mutating operations — what runs where

Rules for keeping multi-agent + multi-worktree work coherent against shared infra. Established 2026-05-25.

**Worktree → local main: ff-merge on completion.** When work in an `EnterWorktree`-isolated branch is clean AND has been tested, ff-merge into the operator's local `main` (`git -C /Users/ghost/Dev/xiiisins/homelab merge --ff-only <branch>`) and `ExitWorktree`. Do NOT `git push` — pushing remains an explicit operator action; this avoids the operator having to `git pull --rebase` to absorb agent commits after the fact. If `--ff-only` refuses (main diverged), flag it rather than force-merging or rebasing without permission. Skip the auto-merge if the change couldn't be tested (UI work without browser test, persistent OS config without reboot validation) — leave the branch + explain why. Sub-agent fanouts still require parent-agent diff review before merge (see "Merging back" above) — this auto-merge default applies only to a top-level agent's own worktree work.

**`terraform apply` runs only from the main checkout, never from a worktree.** `terraform plan` and HCL editing from worktrees are fine. S3 state locking prevents corruption; this rule prevents *intentionality loss* — multiple agents applying different modules in parallel produces outcomes the operator can't reason about post-hoc. Sequence: edit/plan in worktree → ff-merge per above → apply from main checkout. Unconditional.

**`kubectl apply` is never used directly — Flux reconciles state.** Manifests land in `k8s/` via the normal git path; `flux reconcile kustomization …` is the explicit nudge when needed. No `kubectl apply -f` against the cluster from any agent.

**`ansible-playbook` may run from a worktree, but only one at a time across all agents.** Worktrees isolate git state, not the live infra Ansible targets — concurrent playbooks race on SSH (MaxAuthTries from the hardening role), package locks, and notify-handler restarts (e.g. `restart-k3s` firing twice across nodes is catastrophic). Currently coordinated manually via the conversation. Planned mechanization: `flock(2)`-based Python wrapper at `~/.cache/homelab/ansible-playbook.lock` (machine-wide path so every worktree sees it; auto-releases on process death) — deferred until parallel-agent ansible work becomes a recurring pattern. Until the wrapper exists: ask before running `ansible-playbook` if there's any chance another agent is mid-playbook.

---

## Mechanisms

Runtime quick-reference only. Full K3s install/VM detail in [`docs/services/asgard-k3s.md`](docs/services/asgard-k3s.md); gotchas in [`docs/known-issues/k3s-lifecycle.md`](docs/known-issues/k3s-lifecycle.md) + [`networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md).

- **K3s install** — fully IaC via the Ansible `k3s` role. Version pin `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.33.1+k3s1`). `detect-state.yml` sets `k3s_already_healthy` (active + Ready) → install/calico skip on re-run (avoids duplicate-join); `config.yml` always runs (renders template, notifies restart-k3s). Init node default `gondul` (`--cluster-init`) → CPs join → workers last. **CP rebuild of the init node:** `-e k3s_init_node=hlokk` (any healthy CP) + `kubectl delete node <name>` from a survivor first.
- **VM specs** (`locals.{control_planes,workers}` in `terraform/proxmox/asgard-k3s/main.tf` is authoritative) — **CPs** Göndul/Hlökk/Sigrún on Urd/Verd/Skuld: 2 vCPU / 4GB / 10GB, single NIC VLAN 21, tainted `NoSchedule`, sized identically by rule (failover symmetry). **Workers** Einherjar-urd/verd/skuld: 2 vCPU / 16GB / 30GB `scsi0` OS + 50GB `scsi1` xfs `/data` (local-path tier), eth0 VLAN 21 / eth1 VLAN 20 (MetalLB L2, `10.0.20.201/202/203`).
- ⚠️ **Workers are multi-homed** — four landmine fixes in `roles/k3s/tasks/network.yml` (Calico CIDR pin `10.0.21.0/24`, `rp_filter=2`, `route_localnet=1`, VLAN 20 policy routing). Never "harden" them away — full rules in [`docs/known-issues/networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md).

---

## Current build status

*Full phase narratives in [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md). Incident retros in [`docs/incidents/`](docs/incidents/). This list is the at-a-glance state.*

**Foundation:** ✅ UCG-Ultra (VLANs/zones/firewall) · ✅ KPN DMZ → UCG · ✅ Synology (Munin) · ✅ Proxmox `niflheim` (Urd/Verd/Skuld, PVE 9.x) · ✅ PBS (LXC 1101 on Skuld)

**Asgard K3s — core:** ✅ cluster (fully IaC, teardown+rebuild validated) · ✅ Sealed Secrets (keys in 1P) · ✅ Synology CSI (iSCSI) · ✅ Vault (3-node Raft HA, KMS auto-unseal) · ✅ ESO · ✅ MetalLB (L2) · ✅ tigera-operator · ✅ 4a CP NoSchedule taint · ✅ 4b/4c CP topology + Urd/Verd hardware refresh (all three nodes now identical MSI Cubi)

**Asgard K3s — edge + services:** ✅ 5e.1 Traefik + Gateway API + cert-manager · ✅ 5e.2 Cloudflared + apex + WebFinger · ✅ 5e.3/5e.4 Tailscale (OIDC blueprints, LXCs, tailnet split-DNS) · ✅ 5b.2 AdGuard IaC · ✅ Factorio (LXC 1120) · ✅ 5g.2 PG HA (Patroni + HAProxy/keepalived VIP `10.0.10.210`) · ✅ 5g Teamspeak · ✅ Authentik + Redis · ✅ 5i/5i.3 NetBox + TF→NetBox standing pattern · ✅ 8a Observability (VictoriaLogs + VictoriaMetrics + vmagent + vlagent; no Grafana) · ✅ 8c Zabbix (Hugin: server, fleet agent rollout, SAML SSO) · ✅ 5h.2 Hermod notifications · ✅ 5h.3 Semaphore orchestration + drift-check · ✅ 5j Outline + Garage S3

**1.0 stabilization (Waves S1–S7):** ✅ complete 2026-05-31 — pre-build-on-top hardening (validation, recovery, role-debt, infra-health prober, concrete pins, cleanup). Plan: [`docs/operations/1.0-stabilization.md`](docs/operations/1.0-stabilization.md).

**In flight / pending:**
- 🟡 **Phase 6 — Secret mgmt (Vault OIDC) + Frigg watchtower.** Stage 1 (Vault OIDC, `homelab-admin` read-only, `VAULT_ADDR` cut to HTTPS FQDN) ✅. Stage 2 (Frigg HA control-node VM 2900, Vault-backed shim, `claude remote-control` as systemd, remote-host ansible) ✅ core LIVE. ⚠️ **Vault listener TLS flip (`tls_disable=1` → cert-manager internal CA) is GATED BEFORE Phase 7** — see [open-questions](docs/operations/open-questions.md).
- 🟡 **Services** — Startpage ✅, MicroBin ✅, n8n ✅; **Immich remaining**.
- 🔲 Remaining asgard LXCs (Jellyfin — privileged LXC on Urd, QuickSync passthrough)
- 🔲 **Phase 7 — Jotunheim K3s**
- 🔲 Phase 8b — vm-operator migration (VLSingle/VMSingle CRDs + VMServiceScrape)

---

## Known gotchas

Hard-won operational facts — rules, symptoms, diagnostics, recovery commands — live **per-subject in [`docs/known-issues/`](docs/known-issues/)** (migrated out of this file 2026-06-18 to keep runtime context lean). **Read the relevant file before working on its subject** — pre-flight step 3 enforces this. Full "when to read" hints + maintenance rules in [`docs/known-issues/README.md`](docs/known-issues/README.md); incident retros in [`docs/incidents/`](docs/incidents/).

| Subject | File |
|---|---|
| Networking / multi-homed workers | [`networking-multi-homed-workers.md`](docs/known-issues/networking-multi-homed-workers.md) |
| Storage — iSCSI / Synology CSI / local-path / migration | [`storage-iscsi-synology.md`](docs/known-issues/storage-iscsi-synology.md) |
| Garage (S3 object store) | [`garage.md`](docs/known-issues/garage.md) |
| K3s lifecycle / rebuilds | [`k3s-lifecycle.md`](docs/known-issues/k3s-lifecycle.md) |
| Vault | [`vault.md`](docs/known-issues/vault.md) |
| Flux / Helm / Kustomize | [`flux-helm-kustomize.md`](docs/known-issues/flux-helm-kustomize.md) |
| K8s scheduling | [`k8s-scheduling.md`](docs/known-issues/k8s-scheduling.md) |
| Traefik / Gateway API | [`traefik-gateway-api.md`](docs/known-issues/traefik-gateway-api.md) |
| Authentik | [`authentik.md`](docs/known-issues/authentik.md) |
| Cloudflare / Cloudflared | [`cloudflare.md`](docs/known-issues/cloudflare.md) |
| DNS / AdGuard Home | [`dns-adguard.md`](docs/known-issues/dns-adguard.md) |
| Postgres | [`postgres.md`](docs/known-issues/postgres.md) |
| HAProxy / keepalived | [`haproxy-keepalived.md`](docs/known-issues/haproxy-keepalived.md) |
| NetBox (+ e-breuninger/netbox TF provider) | [`netbox.md`](docs/known-issues/netbox.md) |
| Frigg / control-node watchtower | [`frigg-control-node.md`](docs/known-issues/frigg-control-node.md) |
| Ansible / roles | [`ansible-roles.md`](docs/known-issues/ansible-roles.md) |
| LXC / Proxmox (+ PVE host patching) | [`lxc-proxmox.md`](docs/known-issues/lxc-proxmox.md) |
| Tailscale | [`tailscale.md`](docs/known-issues/tailscale.md) |
| SSH / system | [`ssh-system.md`](docs/known-issues/ssh-system.md) |
| Shell / tooling | [`shell-tooling.md`](docs/known-issues/shell-tooling.md) |
| Terraform / state | [`terraform-state.md`](docs/known-issues/terraform-state.md) |
| Observability — VM / vmagent / vmui / vlagent | [`observability.md`](docs/known-issues/observability.md) |
| SFTPGo / Factorio | [`sftpgo-factorio.md`](docs/known-issues/sftpgo-factorio.md) |
| Zabbix (server / agent-API / SAML / S4 prober) | [`zabbix.md`](docs/known-issues/zabbix.md) |
| Caddy reverse-proxy role | [`caddy.md`](docs/known-issues/caddy.md) |
| Semaphore (Ansible scheduler) | [`semaphore.md`](docs/known-issues/semaphore.md) |
| Outline (wiki) | [`outline.md`](docs/known-issues/outline.md) |
| MicroBin (pastebin / file-share) | [`microbin.md`](docs/known-issues/microbin.md) |

**Adding a gotcha** (post-flight): edit the matching file — never paste gotcha text back into this section. New subject → new file + a row above + a row in the known-issues `README.md`. This index carries pointers only.

## Reference

### Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Lexar NM790 (Gen 4, DRAM-less, HMB 3.0) | **Urd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Samsung 970 EVO (Gen 3, DRAM) | **Verd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 512GB NVMe SK Hynix PC300 (Gen 3, DRAM) | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

**Storage tier — etcd fsync consistency: Verd ≈ Skuld > Urd.** Urd's NVMe is DRAM-less HMB Gen 4 (slowest under sustained sync); Verd + Skuld DRAM-equipped Gen 3. All three well within etcd's tolerance, massive improvement over the old Urd mSATA.

All three nodes are now identical hardware (MSI Cubi i3-1215u / 32GB) — Skuld was refreshed off the Beelink N100/16GB (confirmed live 2026-05-31: `PRO ADL-U Cubi 5`, i3-1215U, 31 GiB; kept its 512GB SK Hynix PC300 NVMe). No more single-node resource pressure. Urd long-term plan: dedicated Jellyfin LXC with Intel QuickSync passthrough (i3-1215u UHD Graphics). *(The Skuld refresh predates this note — narrative/date not yet captured in build-sequence; flag for a hardware-doc sweep.)*

Migration narratives + storage-tier verification + phase history: [`docs/architecture/hardware.md`](docs/architecture/hardware.md) + [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md) (Phases 4a/4b/4c) + [`docs/incidents/`](docs/incidents/).

### Network

```
KPN Experia Box (192.168.2.0/24, DMZ) → UCG-Ultra WAN
  └── UCG-Ultra → dumb switches → nodes
```

**MGMT subnet: `10.0.254.0/24`** (NOT `10.0.1.0/24` — earlier drafts had this wrong and nearly caused a correct iSCSI portal to be "fixed").

#### VLANs

| VLAN | Subnet | Name | Purpose |
|------|--------|------|---------|
| 1 | `10.0.254.0/24` | HL-MGMT | Management |
| 10 | `10.0.10.0/24` | HL-ASG-VIP | Asgard VIPs (keepalived) |
| 11 | `10.0.11.0/24` | HL-ASG-SVC | Asgard LXCs |
| 20 | `10.0.20.0/24` | HL-ASG-K3S-VIP | Asgard K3s MetalLB |
| 21 | `10.0.21.0/24` | HL-ASG-K3S-NODE | Asgard K3s nodes (CPs + workers) |
| 30 | `10.0.30.0/24` | HL-JOT-K3S-VIP | Jotunheim K3s MetalLB |
| 31 | `10.0.31.0/24` | HL-JOT-K3S-NODE | Jotunheim K3s nodes |
| 60 | `10.0.60.0/24` | HL-CLIENT | Personal devices |
| 100 | `10.0.100.0/24` | HL-STOR | Storage / NFS |
| 222 | `10.0.222.0/24` | Untrusted | Quarantine |

#### Key IPs

- `10.0.254.1` UCG-Ultra / `10.0.254.11/12/13` Urd/Verd/Skuld / `10.0.254.20` Munin
- `10.0.10.200` AdGuard VIP / `10.0.10.210` PG HAProxy VIP
- `10.0.11.20` PBS / `10.0.11.21` Hugin (Zabbix) / `10.0.11.201/202/203` AGH (Saga/Mimir/Kvasir)
- `10.0.11.213/214/215` Tailscale LXCs (Bifrost/Heimdall/Gjallarbru) / `10.0.11.220` Factorio (1120)
- `10.0.11.230/231/232` PG (Fulla/Vör/Idunn) / `10.0.11.233/234/235` HAProxy+etcd (Hlin/Eir/Snotra)
- `10.0.21.11/12/13` Asgard CP (Göndul/Hlökk/Sigrún) / `10.0.21.21/22/23` workers eth0 (Einherjar-urd/verd/skuld)
- `10.0.20.10` Traefik VIP / `10.0.20.11–.99` MetalLB pool / `10.0.20.201/202/203` workers eth1
- `10.0.31.11/12/13` Jotunheim CP (Rota/Hildr/Kára) / `10.0.31.21/22/23` workers (Drengr-urd/verd/skuld)

Cluster CIDRs: Pod `10.42.0.0/16` (`k3s_pod_cidr` — K3s `cluster-cidr` AND Calico ipPool), Service `10.43.0.0/16` (`k3s_service_cidr`).

#### Resource IDs

- `1101–1199` — Asgard LXCs (1101-1109 backup+mon, 1110-1119 net, 1120-1129 services, 1130-1139 DB+HAProxy)
- `2001–2999` — Asgard K3s VMs
- `3001–3999` — Jotunheim K3s VMs
- `10001+` — Templates

Full per-LXC IP table + DNS naming + firewall posture + physical topology: [`docs/architecture/network.md`](docs/architecture/network.md).

### Naming convention

Norse mythology throughout. **Meta-principle:** primary defines the theme; replicas expand within it.

| Thing | Name |
|-------|------|
| Proxmox cluster | `niflheim` |
| Proxmox nodes | Urd / Verd / Skuld (the Norns) |
| NAS | Munin (Odin's raven of memory) |
| Asgard K3s CP | Göndul / Hlökk / Sigrún (Valkyries) |
| Asgard K3s workers | Einherjar-urd/verd/skuld |
| Jotunheim K3s CP | Rota / Hildr / Kára (Valkyries) |
| Jotunheim K3s workers | Drengr-urd/verd/skuld |
| AdGuard | Saga (primary) / Mimir / Kvasir |
| Asgard PG | Fulla / Vör / Idunn (Frigg's handmaidens) |
| Asgard PG HAProxy/etcd trio | Hlin / Eir / Snotra (Frigg's handmaidens by function) |
| Tailscale LXCs | Bifrost / Heimdall / Gjallarbru |
| Zabbix LXC | Hugin (raven of Thought — pairs with Munin/Memory NAS) |
| DNS zones | `xiiisins.com` (apex, external) / `midgard.xiiisins.com` (internal alias) / `niflheim.xiiisins.com` (internal-only) |

### Repo map

```
homelab/
├── CLAUDE.md
├── docs/                                  # See docs/homelab-design.md for the index
├── .github/workflows/                     # CI — GHCR custom-image builds (build-semaphore-image.yml)
├── docker/                                # Custom container image build contexts (semaphore/ → ghcr.io/xiiisins/*)
├── terraform/
│   ├── proxmox/
│   │   ├── asgard-k3s/                    # K3s VM definitions (bpg/proxmox, API token)
│   │   ├── asgard-lxcs/                   # Normal LXCs (API token — Factorio/PG/HAProxy/AGH/Zabbix)
│   │   └── asgard-lxcs-root/              # Root-pam LXCs (PROXMOX_VE_PASSWORD — Tailscale, future device_passthrough)
│   ├── vault/                             # Vault config (KV, auth methods, policies, roles)
│   ├── cloudflare/                        # Tunnel + DNS records + Vault KV writes
│   ├── authentik/                         # OIDC providers + identity-as-data (users.yaml + groups.yaml)
│   ├── tailscale/                         # Tailnet ACL (policy.hujson) + auth keys
│   ├── netbox/                            # TF→NetBox writes (sites, VMs, IPs, tags) via e-breuninger/netbox
│   ├── adguard/                           # TF→AGH rewrites (gmichels/adguard, write-to-origin Saga)
│   ├── aws/                               # S3 state bucket + IAM (bootstrap; local-state by chicken-egg)
│   └── {dns,k3s}/                         # Scaffolding (empty)
├── ansible/
│   ├── inventory/                         # hosts.yml + group_vars/ (auto-discovered adjacent)
│   ├── playbooks/                         # asgard-k3s, factorio-host, postgres-host, etc.
│   └── roles/                             # baseline / k3s / hardening / postgres / factorio / sftpgo / tailscale
└── k8s/
    ├── asgard/
    │   ├── flux-system/                   # Flux Kustomizations: infrastructure, apps, *-config
    │   ├── infrastructure/                # HelmReleases + CRD-independent resources
    │   ├── <component>-config/            # CRD-dependent resources, dependsOn: infrastructure
    │   └── apps/                          # Leaf consumer workloads
    └── jotunheim/                         # Mirror structure (not yet deployed)
```

Phase-history annotations + per-directory contents: [`docs/services/asgard-k3s.md`](docs/services/asgard-k3s.md) + [`docs/services/jotunheim-k3s.md`](docs/services/jotunheim-k3s.md).

---

## What the owner wants to learn

Kubernetes is the primary goal. Explain the *why* behind K8s design choices, not just manifests. Owner knows Linux, Ansible, networking, enterprise infrastructure patterns — K8s-specific concepts are the knowledge gap. Deep K3s/K8s experimentation is intended for the future jotunheim ("can implode") cluster — asgard is built carefully, not used as a learning sandbox.
