# Homelab — Claude Code context
*Operational runtime context. Full design + history in [`docs/homelab-design.md`](docs/homelab-design.md) (index → `architecture/`, `services/`, `operations/`, `incidents/`, `procedures/`).*

---

## What this is

A ground-up homelab rebuild on 3 physical nodes (Urd / Verd / Skuld). Goals: reliable services for friends/family, K8s learning environment, portfolio at senior/principal infrastructure level. Owner: senior infra (10+ years Ansible, career in IT/sysadmin/platform). Kubernetes is the primary learning gap; everything else is well-known.

**Where to look:**
- [`docs/homelab-design.md`](docs/homelab-design.md) — top-level index
- [`docs/architecture/`](docs/architecture/) — hardware, network, identity-secrets, IaC layering
- [`docs/services/`](docs/services/) — per-service deep dives (asgard-k3s, jotunheim-k3s, LXCs, Factorio, Postgres, Synology)
- [`docs/operations/`](docs/operations/) — build sequence, decisions log, open questions
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
- **IPAM/DCIM: NetBox in asgard K3s.** Source of truth for devices/VMs/LXCs/IPs/VLANs/prefixes (live since 5i, 2026-05-24). Git remains the IaC spec; NetBox is the queryable view. TF→NetBox standing pattern is live in `terraform/netbox/` (e-breuninger/netbox v5.3.0; pinned, see provider gotchas): **every new LXC/VM TF resource MUST have a matching `netbox_virtual_machine` + `netbox_interface` + `netbox_ip_address` declaration in `terraform/netbox/vms.tf` locals** (and the same for physical devices in `devices.tf` if anything new lands). Provider authenticates via admin token from 1P "Asgard - NetBox - admin API token" (no dedicated `terraform` NetBox user — provider has user/token resource incompatibilities; see CLAUDE.md "Terraform — netbox provider" gotchas). NetBox is K8s-fronted via Traefik at `netbox.niflheim.xiiisins.com` (internal-only). Authentik OIDC for login; permissions assigned manually in NetBox UI until SOCIAL_AUTH_PIPELINE override lands.
- **DNS: AdGuard Home, NOT Pi-hole.** Three LXCs (Saga/Mimir/Kvasir), keepalived VIP `10.0.10.200`.
- **AGH rewrites: Terraform-managed in `terraform/adguard/` — never hand-edited in the AGH UI.** New internal DNS records land via the `locals.rewrites` map in `terraform/adguard/rewrites.tf` (FQDN → answer) + `terraform apply`. Provider targets Saga origin (`10.0.11.201`); adguardhome-sync fans writes to Mimir + Kvasir on cron. Provider auth via env (`ADGUARD_HOST`/`SCHEME`/`USERNAME`/`PASSWORD`) loaded by `homelab-env` from 1P "Adguard - admin". Smoketest URL: `curl https://smoketest.niflheim.xiiisins.com/anything` → 200 "smoketest ok" confirms DNS rewrite + Traefik routing + backend healthy end-to-end. (decisions: "AGH rewrites — Terraform managed via gmichels/adguard, write-to-origin")
- **DNS zones: three-zone scheme.** `xiiisins.com` (apex, external, Cloudflare-resolved) / `midgard.xiiisins.com` (internal alias for publicly-reachable services, AdGuard) / `niflheim.xiiisins.com` (internal-only, AdGuard). Each zone gets its own wildcard cert via cert-manager DNS-01 against the same zone-scoped Cloudflare token (`secret/k8s/cert-manager/cloudflare`, scope `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`). (decisions: "DNS — three-zone scheme", "External TLS posture")
- **MetalLB: L2 mode.** Workers are multi-homed (eth0 VLAN 21 / eth1 VLAN 20, eth1 IPs `10.0.20.201/202/203` outside the pool). L2Advertisement uses `nodeSelectors` excluding CP nodes (CPs have no eth1). Four landmine fixes required in IaC — see Known gotchas "Networking / multi-homed workers".
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
- **Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs in asgard K3s.** Zabbix stays LXC for monitoring independence (separate failure domain from K3s). VM + VL live in asgard rather than jotunheim — keeps the log-ingest path in-cluster for the workloads producing logs + sidesteps the jotunheim-deploy timing dependency. VictoriaLogs replaces Loki; VictoriaMetrics replaces Prometheus. **No Grafana** — vmui (built into vmsingle) + the native VictoriaLogs UI cover homelab-scale dashboards; revisit only if cross-source dashboards-as-code becomes a real need. Log shipping via **vlagent** (the official VictoriaLogs project shipper) — DaemonSet on K3s nodes via the `victoria-logs-collector` Helm chart (vlagent with `-kubernetesCollector`, native pod-log discovery), plus an Ansible role deploying vlagent as a systemd binary on LXCs/VMs. Vector + Fluent Bit deliberately NOT used — both have file-rotation correctness issues + the 2026 benchmark shows vlagent at 4-10× lower CPU. Off-cluster shippers (LXCs, VMs) reach VL via an HTTPRoute on the niflheim Gateway (cleaner than a MetalLB LoadBalancer + matches the K8s-fronted-FQDN pattern). vm-operator (`VLSingle` / `VMSingle` CRDs) is the Phase 7b refactor target — Helm chart for now.
- **Ansible: AWX in jotunheim K3s.** 30-min scheduled reconciliation. Vault-backed credentials.
- **PBS: privileged LXC on Skuld.** NFS bind-mounted via Proxmox host.

### Secrets — three stores, one rule
*Human lookup → 1Password. Machine at runtime → HashiCorp Vault. Machine at bootstrap → Ansible Vault.*

- **1Password "Homelab" vault** (humans) — web admin passwords, API tokens, LXC template root passwords, DB admin creds, AppRole creds for the MacBook control node, break-glass user keys, AWS KMS unseal token.
- **HashiCorp Vault** (machines at runtime, asgard K3s) — K8s workload secrets via ESO, Ansible role lookups via AppRole. 3-node Raft HA, AWS KMS auto-unseal, iSCSI storage. Vault listener is `tls_disable = 1` — deliberate (see Known gotchas). Vault config (auth methods, policies, roles, KV) in `terraform/vault/`; SecretIDs NEVER in Terraform state.
- **Ansible Vault** (machines at bootstrap, `group_vars/all/vault.yml`) — narrowly scoped: `k3s_token`, RHEL keys, SSH pubkeys, AWS KMS re-seal copy. Only what's needed BEFORE HashiCorp Vault is reachable.

Vault path convention: `secret/<consumer-domain>/...` for **machine consumers** (`k8s/` for K8s workloads, `ansible/` for Ansible-on-LXCs). Path is independent of which TF module mints the secret — minter writes to consumer's path. Human-consumed secrets go to 1P, not Vault (e.g. Munin Tailscale authkey is UI-minted + 1P-stored, not in Vault). (decisions: "Secrets architecture", "Bootstrap-vs-runtime split", "Vault path convention", "Module ownership of Vault KV secrets")

Scope rule: "things that exist *because the homelab exists*" go in the Homelab vault or Vault. Personal credentials and infrastructure *under* the homelab (Proxmox root, Synology admin, UCG-Ultra, KPN) live in 1Password but **outside** the Homelab vault.

Full details + AppRole bootstrap runbook + control-node fish tooling: [`docs/architecture/identity-secrets.md`](docs/architecture/identity-secrets.md).

### LXC / infra
- **LXC management: two Terraform modules.** `terraform/proxmox/asgard-lxcs/` (API-token auth, same tfvars as `asgard-k3s/`) for normal LXCs; `terraform/proxmox/asgard-lxcs-root/` (root@pam ticket auth, needs `PROXMOX_VE_PASSWORD` env) for LXCs whose create-time config touches API endpoints that only accept ticket auth — today just `device_passthrough` (Tailscale `/dev/net/tun`), future: `fuse`, `keyctl`, additional passthroughs. Don't add root-needing LXCs to the main module — the recurring `op read` foot-gun on every apply is exactly why the split exists. Add new LXC: append to the right module's `lxcs.tf` → `terraform apply` → add to `inventory/hosts.yml` → write role + playbook.
- **LXC bootstrap flow.** Day 1: `terraform apply` → `ansible-playbook ... -e 'ansible_user=root' --tags baseline` → full play as `ansible` (hardening locks root SSH out at the end via `AllowUsers ansible recovery`). Day N: just the full play. Recovery via the `recovery` break-glass user (key in 1Password).
- **Factorio LXC pattern: operator self-service via SFTPGo + reconcile loop.** Template for future operator-managed services (game/voice). Operator never gets shell — SFTP into `/factorio/` + edits JSON control files; root-owned Python reconcile script (systemd timer, 30s) converges actual state. Full design: [`docs/services/factorio.md`](docs/services/factorio.md).
- **Repo: private GitHub.** Secrets never in Git regardless; SealedSecrets used for bootstrap secrets.

---

## Process expectations

Procedural instructions for Claude. Follow on every session before drafting plans or implementations.

### Pre-flight — before proposing new work

When the owner says "let's deploy X" / "what's next?" / "let's plan Y", in order:

1. **Search `docs/` for X.** What does design say? Has a decision been made ([`docs/operations/decisions.md`](docs/operations/decisions.md))? Any constraints?
2. **Scan [`docs/operations/open-questions.md`](docs/operations/open-questions.md) for prereqs of X.** Specifically: does X *depend on* an unchecked task (architecturally — would deploying X be wrong or fragile without it)? Does X *interact* with an unchecked task (would the new workload make a latent issue fire)? Does X *make* a pending task more urgent (was it deferred because nothing exercised the gap — and is X that thing)?
3. **Scan Known gotchas for X and adjacent systems.** Not just "is there a gotcha for X" but "what gotchas hit systems X depends on" (storage class, secret store, networking, DNS, the consuming workload pattern).
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
   - This file (`CLAUDE.md`):
     - "Current build status" — update ✅/🟡/🔲 line
     - "Known gotchas" — add new gotcha classes with rule + Why + recovery commands
     - Architectural invariants / hardware / repo-structure / reference sections — update if anything moved or got resized
   - [`docs/architecture/`](docs/architecture/), [`docs/services/`](docs/services/), code-adjacent READMEs — update if scope shifted

2. **Choose patch vs full file.** Signal-density vs friction:
   - **<30 lines / <5 hunks** → patch (unified diff, `-p1` paths, run `patch --dry-run -p1` against source before delivery; if it fails, fall through to staleness rule below)
   - **>100 lines or restructuring** → full file (deliver via file mechanism, not inline)
   - **30–100 lines** → ask the owner. Default full file if change spans many sections; patch if contiguous edits in 1-2 sections.

3. **Cross-reference between docs.** Don't put a gotcha only in CLAUDE.md if it relates to a decision in `docs/`, or vice versa. Both navigable independently.

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

For the operator's own use, prefer the existing `homelab-env` shim — it loads from 1P + caches without ever echoing the values. That's the right path to recommend in user-facing instructions.

For Claude's own machine commands needing several env vars at once, **source the shim's cache file** rather than calling `op read` per-var:

```bash
# Preferred for any multi-var command (cache is almost always warm in this repo)
. ~/.cache/homelab/env.sh && terraform apply
```

`~/.cache/homelab/env.sh` (POSIX) and `~/.cache/homelab/env.fish` (fish) are written by `homelab-env` and contain every 1P-fetched + static var (`KUBECONFIG`, `VAULT_*`, `ADGUARD_*`, `CLOUDFLARE_API_TOKEN`, `AUTHENTIK_*`, plus `VAULT_TOKEN` if set). TTL 24h. Adding a new var means appending to `__homelab_env_map` / `__homelab_static_env_map` in BOTH shim files + `homelab-env --refresh`.

For Vault: `$(vault kv get -field=<f> secret/<path>)` follows the same pattern. For Ansible Vault: `--vault-password-file` or `ansible-vault view | grep` piped into the consumer, never copy-paste-in-prompt.

**Why:** Transcripts persist (Claude bg-session logs, terminal scrollback, `gh pr view` for any pasted command). Even on a private workstation + private repo, the discipline matters because (a) transcripts get shared during debugging, (b) the owner shouldn't have to mentally redact what they're showing someone, and (c) it normalizes a habit that DOES matter in shared/production contexts.

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
- [`docs/known-issues/`](docs/known-issues/) — planned home for gotchas (currently in CLAUDE.md). Migration pending — see [`docs/known-issues/README.md`](docs/known-issues/README.md). Until then CLAUDE.md "Known gotchas" stays canonical (runtime context for Claude).
- `CLAUDE.md` — process rules + Claude-coding context + gotchas (kept here for runtime context)
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

Brief operational facts needed at runtime. Full details in [`docs/services/asgard-k3s.md`](docs/services/asgard-k3s.md).

### K3s install

Fully IaC via Ansible `k3s` role. Role task order: `prerequisites.yml` → `network.yml` (sysctls + VLAN 20 policy routing) → `detect-state.yml` (skip-install gate) → `config.yml` (template render — runs always, notifies restart-k3s on change) → `install.yml` (skip when healthy) → `calico.yml` (init node only, skip when healthy).

- **Version pin:** `k3s_version` in `roles/k3s/defaults/main.yml` (currently `v1.33.1+k3s1`).
- **Idempotency:** `detect-state.yml` sets `k3s_already_healthy` if `systemctl is-active k3s == active` AND node `Ready`. When true, install/calico skipped. Critical to avoid duplicate-join failures on re-run.
- **`config.yml` runs always** (split from `install.yml` 2026-05-21). Previously bundled config-template tasks were wholesale-skipped on healthy nodes, so config changes never rendered.
- **Bootstrap order:** `k3s_init_node` (default `gondul`) inits with `--cluster-init` → other CPs join → workers last. Enforced via `wait_for` + node-count gates.
- **CP rebuild of init node:** override with `-e k3s_init_node=hlokk` (any healthy CP) so the rebuilt node joins (not `--cluster-init`s a fresh cluster).
- **Stale member cleanup before re-run on rebuilt CP:** `kubectl delete node <name>` from a surviving CP.
- **Config templates:** `config-init.j2` (first CP), `config-server.j2` (joining CPs), `config-agent.j2` (workers). CP configs disable `traefik`, `servicelb`, `local-storage`; set `flannel-backend: none` + `disable-network-policy: true`; cluster-cidr `10.42.0.0/16`, service-cidr `10.43.0.0/16`, TLS SANs, `selinux: true`.
- **kubeconfig** fetched to `~/.kube/niflheim-asgard.yaml` with server addr rewritten to init-node IP.

### K3s VM specs

CP cpu/memory parameterized per-node in `locals.control_planes` map in `terraform/proxmox/asgard-k3s/main.tf`.

**Control planes (Göndul/Hlökk/Sigrún)** — 2 vCPU / 4GB / 10GB disk each, single NIC on VLAN 21, RHEL 9 + K3s + Calico. Göndul on Urd, Hlökk on Verd, Sigrún on Skuld. Tainted `node-role.kubernetes.io/control-plane=:NoSchedule` (Phase 4a). CP sizing identical across nodes by rule — failover symmetry requires it.

**Workers (Einherjar-urd/verd/skuld)** — 2 vCPU / 8GB / 15GB disk, eth0 on VLAN 21, eth1 on VLAN 20 (MetalLB L2 — IPs `10.0.20.201/202/203`), RHEL 9 + K3s + Calico + iscsiadm. (Memory floor was bumped 4G→8G on 2026-05-24 during the Phase 5i.3 work after NetBox+Valkey rolling-restart cascaded an OOM chain — commit d690381. Skuld host is the tightest at 16GB total with multiple LXCs + the worker; acceptable but worth watching.)

⚠️ **Workers are multi-homed.** Four landmine fixes required in IaC, all in `roles/k3s/tasks/network.yml`: Calico autodetection pin (`cidrs: ["10.0.21.0/24"]`), `rp_filter=2` (loose), `route_localnet=1`, and VLAN 20 source-based policy routing (`vlan20-policy-routing.service` systemd oneshot). See Known gotchas "Networking / multi-homed workers".

---

## Current build status

*Full phase narratives in [`docs/operations/build-sequence.md`](docs/operations/build-sequence.md). Incident retros in [`docs/incidents/`](docs/incidents/). This list is the at-a-glance state.*

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
- ✅ Phase 5e.4 — Tailnet DNS (2026-05-24). `terraform/tailscale/dns.tf` — MagicDNS on, split DNS `niflheim.xiiisins.com` + `midgard.xiiisins.com` → AdGuard VIP `10.0.10.200`, search path `niflheim.xiiisins.com`. Apex deliberately NOT split (preserves "one zone, one path"). OAuth client `dns:write` scope added in-place.
- ✅ Factorio LXC (1120) — operator self-service via SFTPGo, reconcile loop (2026-05-16)
- ✅ Fulla / PostgreSQL 1 (LXC 1130) — PG 17, TLS, scram-sha-256, two SUPERUSER management roles in Vault (2026-05-17). Standalone; cluster expansion in flight via Phase 5g.2.
- ✅ Authentik + Redis — Authentik 2026.2.3 + hand-rolled Redis StatefulSet (2026-05-17). Now exposed via Traefik+Gateway+Cloudflared.
- ✅ Phase 5i — NetBox IPAM/DCIM (2026-05-24). NetBox 4.6.1 via chart `8.2.17` (OCI), bundled Valkey standalone, external PG via VIP, internal-only HTTPRoute on niflheim Gateway, Authentik OIDC. Initial inventory imported via web UI per [`docs/procedures/netbox-initial-data-import.md`](docs/procedures/netbox-initial-data-import.md). Surfaced **in-cluster pod-to-MetalLB-VIP class** generalized fix: K3s CoreDNS `coredns-custom` rewrite of K8s-fronted internal FQDNs → Traefik's ClusterIP DNS (avoiding the VIP tromboning). Eleven netbox-chart gotchas documented (extraConfig.secret doesn't propagate to Django settings, existingSecret projects all keys without `optional: true`, Valkey iSCSI fsGroup needs volumePermissions, RWO media PVC shared between server+worker, `small` preset OOMs during migrations, etc.).
- ✅ Phase 5i.3 — TF→NetBox standing pattern (2026-05-24). `terraform/netbox/` module on `e-breuninger/netbox v5.3.0` retrofits all 5i.e hand-imported records via TF 1.5+ `import {}` blocks: 1 site, 1 cluster type, 1 cluster, 4 manufacturers + 4 device types, 12 device roles, 10 VLANs + 10 prefixes, 5 physical devices + interfaces + IPs, 20 VMs + 26 interfaces + 26 IPs + 20 primary_ip bindings, 3 VIP records, 14 tag definitions, 1 custom field — total ~160 resources. Three NetBox-side placement corrections (PBS, idunn, Gjallarbru) landed as part of the retrofit. Two upstream blockers surfaced + documented: chart-deploy gap (`API_TOKEN_PEPPERS` not auto-supplied with `existingSecret` — fix commit b576bd2), provider gap (open issue #349 — integer custom_fields not writable, workaround: type=text + validation_regex). Auth model pivoted from "dedicated terraform user + token" to "reuse admin token throughout" (commit 199b94f) after provider v5.3.0 + NetBox-4.6 v2 token + missing is_superuser blocked the dedicated-user pattern. Worker memory bumped 4G→8G during apply (incident commit d690381) — cumulative workload pressure was the trigger.
- ✅ Phase 7a — Observability stack (2026-05-24/25). VictoriaLogs (chart `0.12.5` → v1.50.0, 50Gi iSCSI, 30d retention) + VictoriaMetrics (chart `0.38.0` → v1.143.0, 100Gi iSCSI, 6mo retention) in asgard `monitoring` ns. vmui+VL UI behind Traefik ForwardAuth (Authentik proxy providers, `/insert/*` exempted on VL HTTPRoute). Internal-only at `logs.niflheim.xiiisins.com` + `metric.niflheim.xiiisins.com`. K8s-layer metrics via vmagent (chart `0.39.0`) + kube-state-metrics (chart `7.4.0` → v2.19.0); Zabbix LXC handles host/LXC-level metrics (clean split, no duplication). K3s pod-log DaemonSet via `victoria-logs-collector` chart `0.3.4`; off-cluster shippers (23 hosts: 3 AGH + 3 Tailscale + 3 HAProxy/etcd + 3 PG + 3 K3s CPs + 3 K3s workers + Factorio + PBS + 3 Proxmox hosts + …) via `ansible/roles/vlagent/` systemd binary. vlagent ingests via `https://logs.niflheim.xiiisins.com/insert/native` (HTTPRoute path-bypassing auth); K3s VM hosts use the companion ClusterIP `victorialogs-ingest` since host-network can't reach MetalLB VIPs. Four vmui dashboards shipped via `customDashboardsPath` ConfigMap. **Phase 7b — vm-operator migration (`VLSingle`/`VMSingle` CRDs + `VMServiceScrape`)** deferred until ServiceMonitor-emitting charts or VMAlert become a need.

- ✅ Phase 5b.2 — AdGuard Home IaC (2026-05-25). Saga/Mimir/Kvasir migrated from manual install to TF-managed LXCs + Ansible roles (`adguard` + `adguardhome-sync` + `keepalived` + source-policy-routing for VLAN 10). TF `import` for the 3 LXCs (specs already matched HCL — only `net1 firewall: 1 → false` drift). Role pins `adguard_version=v0.107.76` (upgrade from v0.107.74), `adguardhome_sync_version=v0.9.0`, cron `*/1` (closes the long-pending interval bump). Operator-customized state (30 rewrites + 1 persistent client + 1 enabled filter) preserved via `adguard_force_overwrite_config: false`. VRID=51, chk_adguard `systemctl is-active` track-script with weight=-50 (validated: stop AGH → priority 100→50 → VIP fails over to Mimir, ~10s settle). Surfaced multiple findings — see "Known gotchas" → AdGuard Home + Ansible/roles (ssh_args section) + Shell/tooling (BSD sed, GNU tar strip-components) + LXC/Proxmox (TF import drift).

**Pending:**
- ✅ Phase 5g.2 — PG HA with Patroni + HAProxy/keepalived VIP (2026-05-24). Patroni cluster `niflheim-pg` 3/3 streaming, HAProxy VIP `10.0.10.210` routes writes to current leader via Patroni REST API `/master` health-check, keepalived floats VIP across Hlin/Eir/Snotra (eth1 VLAN 10, priorities 100/90/80, all-BACKUP election, `chk_haproxy` track-script demotes VIP-holder if HAProxy dies). Authentik cut over to VIP (literal-fulla-IP stopgap retired). Failover validated: kill-leader → election → VIP follows + Authentik survives without operator intervention. Generic data-driven `haproxy` + `keepalived` roles built — reusable for any backend/VIP by supplying group_vars. Four pending PG params landed via orchestrated `patronictl restart` (replicas first: `max_connections=300`, `wal_log_hints=on`, `cluster_name`, `listen_addresses`).
- ✅ Phase 5g — Teamspeak (asgard K3s, 2026-05-25). Pivoted from planned LXC 1121 → K3s app (`k8s/asgard/apps/teamspeak/`): TS3 3.13.7 single-replica StatefulSet, PG via Patroni HAProxy VIP `10.0.10.210` (first-party `ts3db_postgresql` plugin, `sslmode=require`), MetalLB VIP `10.0.20.12` shared between UDP voice + TCP filetransfer (`metallb.universe.tf/allow-shared-ip: teamspeak`), ETP=Local for source-IP preservation. SRV ring `_ts3._udp.ts3.xiiisins.com` (existing, outside TF) → `hel-ts3` (homelab) + `do-ts3` (DigitalOcean fallback). PG provisioned via postgres-common-databases (`teamspeak3@teamspeak3`, password TF-minted dual-pathed). Four gotchas surfaced — chown init CAP_CHOWN/CAP_FOWNER (extends existing chown-init rule), StatefulSet RollingUpdate won't replace CrashLoopBackOff pod, Ansible dynamic-include doesn't propagate `--tags` + strict-mode boolean conditional rules, MetalLB IPAddressPool static/dynamic collision.
- ✅ Phase 7c.1/7c.2/7c.7/7c.8 + Round 2 — Zabbix server + cluster-wide agent rollout + monitoring depth (Hugin, 2026-05-25). LXC 1102 on **Urd**, sizing 2 vCPU / 4GB / 8GB disk; identity **Hugin** (raven of Thought, pairs with Munin/Memory NAS). Zabbix 7.0 LTS, PHP 8.4 pinned, PG backend via Patroni HAProxy VIP. **7c.8:** agent2 deployed to all 23 inventory hosts via OS-aware role split (Debian apt + RHEL 9 dnf for K3s VMs + SELinux boolean). Declarative-per-host registration via `community.zabbix.zabbix_host` with httpapi connection (community.zabbix 4.x dropped env-var auth) — recasts 7c.7's planned auto-registration-action as inventory-as-truth. Hierarchical host groups (`Asgard/K3s/CP`, `Asgard/LXCs/Postgres`, `Hypervisors/Proxmox`, etc.) created from inventory aggregation via shared `ansible/playbooks/zabbix-host-groups.yml`. **Round 2 monitoring depth:** `PostgreSQL by Zabbix agent 2` (zbx_monitor PG role with `pg_monitor` membership + agent2 plugin package), `HAProxy by Zabbix agent` (web.page.get → localhost:9000), `Etcd by HTTP` (server-side scrape of each node's :2379/metrics), `Proxmox VE by HTTP` (TF-managed user/token via new `terraform/proxmox/zabbix-access/` module), `Nginx by Zabbix agent` + `PHP-FPM by Zabbix agent` (hugin's frontend, localhost stub_status + status/ping). 3478/3521 (98.8%) items healthy post-deploy. 20 deploy findings encoded in Known gotchas → Zabbix section below. Full retro: [`docs/incidents/2026-05-25-zabbix-server-deploy.md`](docs/incidents/2026-05-25-zabbix-server-deploy.md).
- 🔲 Phase 7c.3–7c.6 — SAML SSO + Traefik fronting + 3-path validation. Local-Admin via `hugin-direct.niflheim.xiiisins.com` is the only auth path until this lands.
- ✅ Phase 5j — Outline wiki + Garage S3 (2026-05-26). First homelab S3 layer: Garage v2.3.0 single-node (RF=1, dangerous-consistency, LMDB, 10Gi meta + 200Gi data on iSCSI) in `k8s/asgard/infrastructure/garage/` — chosen over MinIO (OSS pivot) + SeaweedFS (POSIX/WebDAV redundant with existing iSCSI+NFS). Layout-init Job drives Garage admin API v2 from an alpine+curl+jq sidecar (dxflrs image is FROM scratch, no shell). `terraform/garage/` (jkossis/garage v1.0.4) mints bucket+key+grant + Vault `secret/k8s/outline/s3` via `kubectl port-forward` to the admin ClusterIP. Outline 1.8.0-1 (`outlinewiki/outline` on Docker Hub — NOT `ghcr.io/outline/outline`) in `k8s/asgard/apps/outline/`: 1-replica Deployment (Recreate strategy), UID 1001 numeric, sidecar Redis on emptyDir (Synology DS223J hit a 10/10 iSCSI LUN cap — Redis state is cache-class so emptyDir is the right tradeoff; cap investigation tracked in open-questions). Three hostnames live: `wiki.xiiisins.com` (external via cloudflared), `wiki.midgard` + `wiki.niflheim` (LAN via AGH→Traefik). PG on Patroni VIP with `?sslmode=no-verify` (Outline's pg-connection-string+sequelize now treats `require` as `verify-full` and rejects self-signed). OIDC via Authentik with `outline-users` group gate (declarative per-user via `terraform/authentik/users.yaml` `groups:` list). Two ExternalSecrets split by resolve-time (`outline-redis-secret` day-1, `outline-app` once garage+authentik TF apply). 16 deploy findings encoded in Known gotchas. Full retro: [`docs/incidents/2026-05-26-outline-garage-deploy.md`](docs/incidents/2026-05-26-outline-garage-deploy.md). Service deep-dive: [`docs/services/garage.md`](docs/services/garage.md) + [`docs/services/outline.md`](docs/services/outline.md).
- 🔲 Remaining asgard LXCs (Jellyfin)
- 🟡 Phase 5h.2 — Notifications hub (Hermod, 2026-05-25, partial). LXC 1103 on Verd at 10.0.11.22, AppriseAPI native install (pip+venv+gunicorn+systemd; explicitly no Docker — avoids the 5aff1dd rolled-back Docker-in-LXC attempt). Caddy reverse-proxy on `:80` with `remote_ip` allowlist matcher gates inbound POSTs; AppriseAPI bound `127.0.0.1:8000`. Four Discord channels with Valkyrie display names: Hrist (`tag: critical`), Mist (`tag: alert`), Ölrún (`tag: media`), Hel (untagged-quarantine). Generic `caddy-reverse-proxy` Ansible role built for reuse (any future LXC needing IP-allowlisted ingress to a single upstream). Vault layout: `secret/ansible/hermod/config-key` (TF-minted 32-char random_password, soft-auth depth) + `secret/ansible/hermod/discord/<tag>` (operator-minted, single `url` field). End-to-end smoketest validated (4 positive POSTs → HTTP 200 + Discord delivery; 1 disallowed → HTTP 403). 5h.2.a-h + 5h.2.k done; **5h.2.i (Zabbix media-type via `community.zabbix.zabbix_mediatype`) + 5h.2.j (Patroni callbacks, PBS hook) still pending** — both are source-side producer wiring, not Hermod-internal. Retro: [`docs/incidents/2026-05-25-hermod-deploy.md`](docs/incidents/2026-05-25-hermod-deploy.md). Full design + JSON schema + source→tag mapping in [`docs/services/notifications.md`](docs/services/notifications.md).
- 🔲 Phase 5h.3 — Ansible orchestration (Semaphore + drift-check). Push-mode scheduler in asgard K3s + per-host-group playbook rename (`asgard-<service>.yml` + `site.yml` orchestrator + multi-play files where `serial:` differs) + NetBox dynamic inventory (`group_by: tag`, jsonfile cache 4h, static `hosts.yml` retained as DR fallback) + drift-check loop (`--check --diff site.yml` every 6h, results → Hermod `alert` on diff/failure, no scheduled auto-apply). Sequenced after 5h.2 because drift-check is the first concrete `alert`-tag producer. Design + decisions in [`docs/architecture/ansible-orchestration.md`](docs/architecture/ansible-orchestration.md).
- 🔲 Jotunheim K3s
- 🔲 Services (Immich, n8n, Privatebin, Startpage)
- 🔲 Phase 7b — vm-operator migration (VLSingle/VMSingle CRDs + VMServiceScrape)

---

## Known gotchas

*Incident retrospectives in [`docs/incidents/`](docs/incidents/). These entries are the rules + recovery commands.*

### Networking / multi-homed workers

- **Calico autodetection on multi-homed workers**: default `firstFound` binds the overlay to eth1 (VLAN 20), breaking cross-node vxlan. Fix in Ansible Calico template: `nodeAddressAutodetectionV4: cidrs: ["10.0.21.0/24"]`. Do NOT revert to firstFound.
- **rp_filter must be loose (`2`)** on multi-homed workers. Strict mode silently drops MetalLB LoadBalancer traffic arriving on eth1. `roles/k3s/tasks/network.yml` sets it on `all` + `default`. Do NOT "harden" back to 1.
- **route_localnet=1** required. MetalLB L2 only ARPs for the VIP — without `route_localnet=1` the kernel drops packets to the unbound IP. Set on `all` by `roles/k3s/tasks/network.yml`.
- **VLAN 20 source-based policy routing.** Reply packets from MetalLB VIPs originate from worker eth1 IP; without policy routing they exit via eth0 → asymmetric → UCG stateful firewall drops. Fix: `vlan20-policy-routing.service` systemd oneshot (`from 10.0.20.0/24 lookup vlan20` + `default via 10.0.20.1 dev eth1 table vlan20`). OS-independent (pure ip(8) + systemd). **Same class on HAProxy/etcd LXCs (VLAN 10 VIP, VLAN 11 default route)** — generalised into the `keepalived` role's `keepalived_source_policy_routing` option (N-entry, see HAProxy / keepalived gotchas).
- **MetalLB L2 election needs `nodeSelectors`** excluding CP nodes (CPs have no eth1 — election there announces nowhere).
- **MetalLB IPAddressPool static/dynamic collision** when a chart's `LoadBalancer` Service has no `loadBalancerIP` set. Vault's UI Service is `serviceType: LoadBalancer` with no static IP → MetalLB dynamic-allocates from the pool bottom (currently `10.0.20.11`). New static-IP claims must avoid that band, OR Vault's allocation could shift on Service recreate. Currently working around per-service (Teamspeak claimed `10.0.20.12` adjacent, with a comment in `service-voice.yaml` documenting why). **Longer-term fix**: pin Vault UI to a static `loadBalancerIP` + carve `IPAddressPool` into two pools — static (`.10-.49`) for explicit-claim services, dynamic (`.50-.99`) for chart-defaults-with-no-IP. Deferred until more LB Services land. Surfaced 2026-05-25 Phase 5g.
- **MetalLB-announced VIPs do not respond to ICMP.** L2 only ARPs; ICMP-to-VIP hits the elected node with no kube-proxy DNAT → kernel returns Destination Host Unreachable. **Test reachability with TCP** (curl/nc on a defined port), never ping.
- **K3s host network can't reach MetalLB-announced VIPs from the same cluster.** Host-network processes on a K3s node (anything running in the root netns, e.g. systemd binaries, host-installed agents) trying to dial the Traefik / VL / etc. VIP from `10.0.20.x` hang — MetalLB ARPs for the VIP but the kernel on the announcing node has no kube-proxy DNAT to back-door the packet to the actual pod from the same node's root namespace. **Fix**: companion `ClusterIP` Service (if the chart's primary is headless, add a sibling one). ClusterIP IS reachable from K3s hosts (it's how `kubectl` itself works). Applied for vlagent-on-K3s-VM-hosts → `victorialogs-ingest` ClusterIP `10.43.14.105:9428` overridden via `group_vars/asgard_k3s.yml`. Same class as the existing "Cloudflared targets ClusterIP DNS, NEVER MetalLB IPs" rule but extended to anything running in a K3s node's root netns. Surfaced 2026-05-24 Phase 7a.
- **RHEL SELinux confines log-collector / file-watcher DaemonSet containers even as `runAsUser: 0`.** Pods that need to read host paths (`/var/log/pods`, `/var/log/containers`, etc.) hit denials despite running as root inside the container. `runAsUser: 0` alone is not sufficient — SELinux MCS context still applies. **Fix**: `privileged: true` on the DaemonSet pod spec + `runAsUser: 0` + `fsGroup: 0`. Privileged is a reasonable tradeoff for a DaemonSet whose whole job is reading host paths. Applied to `victoria-logs-collector` (vlagent K8s DaemonSet). Don't apply to non-log-collection workloads — privileged is a real escalation. Surfaced 2026-05-24 Phase 7a.
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
- **Synology CSI iSCSI volumes need a chown initContainer for non-root pods.** Synology CSI doesn't honor `securityContext.fsGroup` reliably. Pattern: busybox initContainer as UID 0 running `chown -R <uid>:<gid> /mountpath` before the main container. Currently shipped in Vault (`vault-data-chown`) + Authentik Redis (`redis-data-chown`) + VictoriaLogs (`vl-data-chown`) + Teamspeak (`data-chown`). **Sub-gotcha**: the init container must keep default capabilities — `capabilities.drop: ["ALL"]` strips `CAP_CHOWN` + `CAP_FOWNER` even when `runAsUser: 0`, so recursive chown of root-owned files (e.g. fresh ext4's `lost+found`) fails with `Operation not permitted`. Mirror the Vault pattern: minimal securityContext (`runAsUser: 0`, `runAsNonRoot: false`, `allowPrivilegeEscalation: true`), no `drop`. Short-lived container; hardening surface gain isn't worth the breakage. Surfaced 2026-05-25 Phase 5g (Teamspeak first-deploy).
- **Vault SKIP_CHOWN + iSCSI fsGroup quirk.** Chart sets `SKIP_CHOWN=true` when non-root; fsGroup doesn't apply on iSCSI. Pod-level `securityContext { runAsUser: 100, runAsGroup: 1000, fsGroup: 1000, runAsNonRoot: false }` + `extraInitContainer vault-data-chown` (busybox UID 0) running `chown -R 100:1000 /vault/data && chmod 750 /vault/data`.
- **VictoriaLogs (`victoria-logs-single`) chart's init-container values key is `server.initContainers`, NOT `extraInitContainers`.** Setting `extraInitContainers` is silently ignored (no error, no warning) and the pod crashes at startup on permission-denied writing to `/storage`. Same fsGroup-on-iSCSI quirk as Vault + Authentik Redis — busybox UID 0 chown init does the job. Surfaced 2026-05-24 Phase 7a deploy.
- **RHEL 9 e2fsprogs 1.46.5 cannot fsck Synology-CSI-formatted iSCSI LUNs.** CSI uses newer ext4 features (`orphan_file`, ro-compat bit 16) → `unsupported feature(s): FEATURE_C12 FEATURE_R16`. Workaround: fsck from Alpine 3.20 debug pod. `kubectl debug node/<worker> -it --image=alpine:3.20 --profile=sysadmin -- sh` then `apk add --no-cache e2fsprogs && fsck.ext4 -y /dev/sdX`. Revisit at RHEL 10.
- **CSI eviction from tainted CPs is a footgun**, not a free side effect. CP NoSchedule taint evicts the CSI node-plugin DaemonSet; stateful pod still on the CP can't unmount (kubelet retries forever — `csi.san.synology.com not found`). `VolumeAttachment` stays `ATTACHED=true NODE=<dead-CP>` → Multi-Attach errors elsewhere. **Mitigation:** drain stateful workloads BEFORE applying CP taint, OR add CP-taint toleration to CSI DaemonSet. Recovery: iscsiadm logout from affected CP via debug pod, force-delete pod, manually delete stale VolumeAttachment.
- **Synology DS223J iSCSI LUN cap is per-Volume, not per-DSM, and the default is low.** Surfaced 2026-05-26 during Outline+Garage deploy: provisioning the 11th PVC hit `Failed to create LUN, err: Number of LUN reach limit` even though DSM spec sheets suggest much higher caps. All 10 existing LUNs were legitimate (bound to PVs, in use) — no orphans to clean. **Diagnostic**: `kubectl get pv` count vs `iscsiadm -m discovery -t st -p 10.0.254.20:3260` IQN count — equal = at the wall. **Mitigation**: side-step iSCSI for cache-class state (Outline Redis moved to emptyDir). **Structural fix pending** — investigate the per-Volume cap in DSM SAN Manager → iSCSI Manager → Volume settings (UI-raisable). Tracked in open-questions. Generalisation: any new K8s app with small companion PVCs is currently LUN-budget-bound; default emptyDir for ephemeral cache/queue state until the cap is lifted.
- **First-deploy mkfs.ext4 on Synology iSCSI LUNs is TRIM-bound at ~10GB/min over 1GbE.** 200Gi data LUN takes ~20 min, 50Gi ~5 min, 10Gi <1 min. mkfs sits in D-state issuing discard (TRIM) commands to the LUN before laying down filesystem metadata. Symptom from the kubelet side: `MountVolume.MountDevice failed ... rpc error: code = DeadlineExceeded` retries during the formatting window. Synology's TRIM is the bottleneck — kernel is just waiting. **Diagnostic**: on the elected worker, `cat /sys/block/sdX/stat` field 14 (sectors discarded) grows ~20M sectors per minute. **Implication**: any bootstrap Job that depends on a fresh PVC must size its timeouts (`backoffLimit`, per-attempt wait loops) to the LUN size — Garage's `layout-init-job.yaml` uses `backoffLimit: 30` with 5-min waits for ~2.5h total budget against the 200Gi data PVC.
- **mke2fs "apparently in use by the system; will not make a filesystem here!" during CSI format can be transient.** Surfaced during Outline+Garage deploy: meta-garage-0 first format failed with that exact message, second retry succeeded. Likely a race between Synology LUN-init finalisation + kernel block-layer device probing. If a single retry doesn't resolve, check `lsblk -f /dev/sdX` for any lingering FS signature + `blkid /dev/sdX` for a stale UUID — wipe with `wipefs -a /dev/sdX` from a debug pod if so. Don't reach for force-format prematurely; first retry handles most cases.

### Garage (S3 object store)

- **`dxflrs/garage:vN.Y.Z` is `FROM scratch` — no shell, no debug tools.** First-draft layout-init Job used `command: [sh, -c, ...]` against the garage image and errored at start with `exec: "sh": executable file not found in $PATH`. Drive any side-channel logic against Garage from outside the image — alpine+curl+jq is the standard pattern, hitting the [admin API v2](https://garagehq.deuxfleurs.fr/api/garage-admin-v2.html) (`/v2/GetClusterStatus`, `/v2/GetClusterLayout`, `/v2/UpdateClusterLayout`, `/v2/ApplyClusterLayout`) with `Authorization: Bearer ${GARAGE_ADMIN_TOKEN}`. `kubectl exec` is dead against scratch images by definition — admin API or nothing.
- **Garage `/health` (admin port :3903) returns 503 (degraded) until at least one node has a role assigned in the layout.** Readiness probe pointed at `/health` will fail until layout is applied. Chicken-and-egg: layout-init Job needs to reach the admin API to assign the layout, but Service DNS for headless StatefulSet pods isn't published until pods are Ready. **Fix**: `publishNotReadyAddresses: true` on the headless `garage-headless` Service. Layout-init can then resolve `garage-0.garage-headless...` while garage-0 is still not-Ready, assign the role, and the readiness probe naturally turns green. Generalisable to any service whose bootstrap interface is gated by its own readiness criteria.
- **Garage layout-init Job is idempotent via the API's version field.** `GET /v2/GetClusterLayout` returns `{version, roles, ...}`. Fresh cluster = version 0; assigned + applied = version >= 1. Script's first check skips out if version >= 1, so re-applies (Flux reconcile of an unchanged Job, or post-`kubectl delete job` retries) are no-ops. To force a re-run (e.g. layout change after scale-out): edit the Job's body in Git, `kubectl delete job garage-layout-init`, `flux reconcile kustomization infrastructure`. The Job is immutable post-creation (see Flux/Helm/Kustomize gotcha) so spec edits without delete are silently rejected.
- **jkossis/garage Terraform provider v1.0.4 schema diverges from common assumption.** Provider block: `endpoint = "http://host:port"` (single field), NOT split `host`/`port`/`scheme`. Bucket resource: `global_alias = "name"` (single string), NOT `global_aliases = ["name"]` (list). Access-key resource: the `id` attribute IS the access_key_id (the provider doesn't expose an `access_key_id` field directly — bucket_permission uses `garage_key.X.id`). Diagnose any schema mismatch with `terraform providers schema -json` — first-line tool for any provider whose docs drift across versions. Surfaced 2026-05-26 during `terraform/garage/` first apply.
- **Garage admin API access is ClusterIP-only by design.** No external LoadBalancer; operator workflow is `kubectl port-forward -n garage svc/garage-admin 3903:3903 &` before `terraform plan/apply` in `terraform/garage/`. Documented in [`docs/services/garage.md`](docs/services/garage.md) "Operator workflow". Don't add a Gateway HTTPRoute for the admin port — admin_token is the only auth and exposing it externally is a posture downgrade.

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
- **`VAULT_TOKEN` in `~/.cache/homelab/env.sh` is per-session and can expire mid-deploy.** Symptom during a long apply chain: `terraform plan/apply` errors with `* permission denied / * invalid token` against any `vault_*` resource. Re-mint: `fish -c 'homelab-env; set-vault-token root; homelab-env --refresh'` (the shim's `set-vault-token root` mode reads the root token from 1P and writes it into the cache files; `--refresh` is needed to actually persist; `homelab-env` first because `set-vault-token` depends on `VAULT_ADDR` from the cache). Verify with `vault token lookup`. **Long-deploy rule**: any chain that spans more than ~2h should re-verify VAULT_TOKEN before each TF module's apply — token-expiry mid-flight is a quiet failure that costs a full plan-and-retry cycle. Tracked as a follow-up in open-questions (`homelab-preflight` shim idea).

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
- **subPath-mounted ConfigMap/Secret keys do NOT auto-update in running pods.** Kubelet snapshots subPath mounts at pod start; subsequent ConfigMap changes are reflected in the in-cluster object (Flux + Kustomize apply fine) but the file inside the pod still shows the old content. Symptom: change a Caddyfile / nginx.conf / app config, Flux reconciles cleanly, behavior doesn't change. **Fix:** `kubectl rollout restart deployment <name> -n <ns>` after any subPath-mounted ConfigMap change. Examples in-tree: `apex-static` (Caddyfile + webfinger.json via subPath). Defenses if churn becomes painful: (a) drop subPath, mount the whole ConfigMap dir + adjust the consumer's config path; (b) add a stakater/reloader annotation; (c) document the rollout-restart step in the consumer's README.
- **CoreDNS forwards to `/etc/resolv.conf` from each replica's node.** Every node needs a working resolv.conf. Node resolv.conf changes need `kubectl rollout restart deploy -n kube-system coredns` to take effect.
- **Concrete-pin all IaC versions, no floats.** Helm + TF providers + Ansible role versions. `~> 4.0` / `0.x` / `2.x` all banned — they cover patch versions which proved breaking (metallb 0.16 unconditional ServiceMonitor; synology-csi 0.11.2 → unpublished `v1.3.0` image). Current asgard pins: sealed-secrets 2.18.6, vault 0.32.0, external-secrets 0.20.4, metallb 0.15.3, synology-csi 0.11.1, authentik 2026.2.3.
- **MetalLB chart 0.16+ requires explicit `prometheus` values block.** Unconditional `.Values.prometheus.serviceMonitor.enabled` reference; template render fails without it. Either pin `0.15.x` or set `prometheus.serviceMonitor.enabled: false`.
- **synology-csi chart 0.11.2 references unpublished `synology/synology-csi:v1.3.0`** on Docker Hub. Pin `0.11.1` (appVersion `v1.2.1`). Revisit when 1.3.0 actually ships.
- **Jobs are immutable — `spec.template` edits require `kubectl delete` first.** Flux fails the Kustomization with `Job.batch "X" is invalid: spec.template: Invalid value: ...: field is immutable` when you change the script, image, env, or anything else inside the pod template of an already-created Job. Fix: `kubectl delete job/<name>` then `flux reconcile kustomization <ks>` — Flux recreates from Git with the new spec. `backoffLimit`, `parallelism`, `completions`, `suspend`, `manualSelector` are also immutable in some k8s versions; safest assumption is "Job spec edits = delete + reconcile" cycle. Surfaced 2026-05-26 during Outline+Garage deploy on the layout-init Job rewrite (scratch-shell → alpine).
- **ExternalSecret `data` refs are all-or-nothing — one missing Vault path blocks the entire target Secret.** ESO refuses to materialize a target Secret until EVERY `data[i].remoteRef` resolves; a single `Secret does not exist` error keeps the rest of the bundle in `SecretSyncedError`. Consumers waiting on the partially-resolvable subset stay stuck. **Fix**: split into multiple ExternalSecrets by resolve-time-cohort. Pattern in `k8s/asgard/apps/outline/externalsecret.yaml`: `outline-redis-secret` (only the day-1-available `k8s/outline/app` path → renders the Secret Redis needs) + `outline-app` (all four paths → renders the Secret Outline needs once garage + authentik TF apply land). Generalisation: any time consumers have different readiness windows against the same Vault tree, give them separate ExternalSecrets — chaining onto one bundle propagates the slowest-to-arrive ref's latency to every consumer.
- **ESO refresh runs on `refreshInterval` only — force a sync via annotation.** Default `refreshInterval: 1h` means a newly-minted Vault value takes up to an hour to reach the K8s Secret. Force-sync: `kubectl annotate -n <ns> externalsecret <name> force-sync=$(date +%s) --overwrite`. ESO watches that annotation and re-resolves immediately. After force-sync, the consumer pod still needs `kubectl rollout restart` to pick up new env vars (Kubernetes Secret-as-env is snapshotted at pod start — same class as the subPath-mount gotcha just above, but for envFrom secretRef instead of volume mounts).

### K8s scheduling

- **Required pod anti-affinity + RollingUpdate without `maxSurge: 0` deadlocks on N replicas across N nodes.** Default `maxSurge: 25%` rounds up to 1; cluster has no 4th slot → rollout deadlocks. Fix: explicit `strategy.rollingUpdate.maxSurge: 0, maxUnavailable: 1`. Briefly runs at N-1/N during rolls.
- **YAML key casing is silently dropped** when unknown to the schema (e.g. `rollingupdate` vs `rollingUpdate`). Default permissive admission accepts-then-ignores. Debug "my override isn't taking" with `kubectl get ... -o yaml` and grep for the actual key — if missing, it's a casing/spelling issue, not logic.
- **StatefulSet `RollingUpdate` won't replace a CrashLoopBackOff pod.** The controller waits for the existing pod to be `Ready` before deleting it for the new revision (avoids cascade-deletion thrashing); a permanently-failing init container never reaches Ready, so the rollout stalls indefinitely with `UpdateRevision != CurrentRevision` and the old (broken) pod still running. **Fix:** `kubectl delete pod <name> --grace-period=0 --force` to break the deadlock; the new pod spawns with the updated template. Affects StatefulSets specifically — Deployment's RollingUpdate has different semantics (new RS creates fresh pods independently). Common after first-deploy fixes where the broken pod is on the old template. Surfaced 2026-05-25 Phase 5g.
- **`runAsNonRoot: true` fails admission against images whose USER directive is a name (not a numeric UID).** `Error: container has runAsNonRoot and image has non-numeric user (nodejs), cannot verify user is non-root`. K8s can't introspect the image's user db to confirm the named user is non-root, so it refuses to start. **Fix**: set `runAsUser: <numeric>` explicitly alongside `runAsNonRoot: true` — admission then verifies the UID directly. Common offenders: `outlinewiki/outline` (USER nodejs = 1001), `library/postgres` (USER postgres), `bitnami/*` (USER 1001 by name in older images). For ANY new image where the upstream USER might be name-based, set numeric UID even if you don't need it — costs nothing, future-proofs against the admission failure. Surfaced 2026-05-26 Phase 5j (Outline first-deploy).

### Traefik / Gateway API

- **Traefik chart v39+ replaced shorthand entrypoint syntax with upstream nesting.** Old `ports.web.redirectTo` / `ports.websecure.tls.enabled` removed → use `ports.web.http.redirections.entryPoint` / `ports.websecure.http.tls: {}`. Service block upstream-aligned in v40.
- **Traefik chart has no `kubernetesIngressRoute` toggle.** `kubernetesCRD` handles ALL Traefik CRDs together (IngressRoute, Middleware, etc). To use Gateway API only: keep `kubernetesCRD: enabled: true` for Middleware CR support, just don't create IngressRoute CRs.
- **Traefik Gateway listener `port:` matches the entrypoint's internal listen port**, NOT the Service `exposedPort`. Either set Gateway listeners to chart defaults (`8000`/`8443`) — muddy — or grant `NET_BIND_SERVICE` and bind 80/443 directly. We use the latter; Gateway manifests read naturally.

### Authentik

- **Authentik chart values block does NOT override env vars** — env vars win silently. Set service config via ExternalSecret env vars (`AUTHENTIK_POSTGRESQL__HOST`/`__SSLMODE`/`__PORT`/`__USER`/`__NAME`), treat values block as default-documentation only.
- **Authentik brand `default: true` is mutually exclusive cluster-wide.** Blueprint must demote shipped `authentik-default` (set `default: false`) before claiming default on your own. Both entries can co-exist in the same file; blueprints apply in document order.
- **Authentik blueprints — brand for branding, NOT tenants.** Don't include `authentik_tenants.tenant` unless you genuinely want multi-tenant Postgres-schema isolation. Single-instance deploys conflict with the implicit default tenant.
- **Authentik OIDC discovery is per-app under `/application/o/<slug>/.well-known/openid-configuration`**, NOT host root. `https://authentik.<host>/.well-known/openid-configuration` is a hard 404. For WAF/CDN skip rules: use `/application/o/*` prefix (covers per-app discovery + authorize + token + userinfo + jwks + end-session).
- **Authentik embedded outpost has its own `config.authentik_host` that bypasses brand resolution.** Brand `default: true` controls which domain shows in the apex UI + WebFinger + most flows, but the outpost (used for proxy-provider / ForwardAuth flows) caches its OWN host URL at startup as `config.authentik_host`. Symptom: after changing the brand domain, login buttons on protected apps (vmui, VL UI, anything behind a proxy provider) still redirect to the OLD host. **Fix**: API PATCH the outpost UUID setting `config.authentik_host = https://<new-host>` + restart `authentik-server` pods. Make it declarative: blueprint `02-embedded-outpost.yaml` pins `config.authentik_host` so cluster rebuild reconstitutes the value. Surfaced 2026-05-24 Phase 7a — the auth bug for vmui/VL UI took longest to find because brand-vs-outpost-host is a non-obvious second source of truth.
- **Per-app group binding policies fail closed for non-members — error message is opaque.** `authentik_policy_binding` referencing a group as the policy target denies any user not in that group with `Permission denied. Request has been denied. Explanation: Policy binding 'None' returned result 'False'`. The "None" is literal — it's not a missing variable, it's how Authentik renders an anonymous group-membership policy in the deny page. **Fix**: add the user to the per-app group via `terraform/authentik/users.yaml` `groups:` list + `terraform apply` (membership is declared on the user side, computed inversely for groups). Reaffirm: every per-app gate is opt-in for each user; new app == add the new group to the operator's `groups:` list at deploy time, don't wait for the post-deploy login failure to remember. Surfaced 2026-05-26 Phase 5j (Outline first login).

### Cloudflare / Cloudflared

- **Cloudflared targets backend Services by ClusterIP DNS, NEVER MetalLB IPs.** Cloudflared runs in-cluster; ClusterIP DNS keeps traffic in pod network. MetalLB-IP targeting creates in-cluster tromboning. **Exception:** to apply Traefik middleware to externally-tunnelled traffic, target Traefik's ClusterIP DNS with `originRequest.httpHostHeader: <fqdn>` + `noTLSVerify: true`. **Generalisation:** the same rule applies to any pod that needs to reach a K8s-fronted internal FQDN — see the CoreDNS rewrite pattern in the DNS section ("In-cluster K8s-fronted FQDNs"), which is the cluster-wide structural answer for workloads that don't have Cloudflared's app-level config flexibility.
- **Cloudflare Free plan `Bot Management:Edit` is a separate API token scope** — NOT folded under `Zone Settings:Edit`. The `cloudflare_bot_management` TF resource requires its own `Zone:Bot Management:Edit`. Current `terraform-cloudflare` token scopes: `Zone:DNS:Edit + Zone:Zone:Read + Zone:Zone Settings:Edit + Zone:WAF:Edit + Zone:Bot Management:Edit` (all on `xiiisins.com`) + `Account:Cloudflare Tunnel:Edit`.
- **Cloudflare provider v5 ruleset import requires `zones/` discriminator prefix.** v4 accepted `<zone_id>/<ruleset_id>`; v5 demands `zones/<zone_id>/<ruleset_id>` (or `accounts/<account_id>/<ruleset_id>`). Error is explicit: `invalid discriminator segment`.
- **CF API token rotations don't notify consumers — verify with `/user/tokens/verify` after any rotation event.** 1Password stores tokens but can't validate them, so a stale value (rotated upstream but never propagated to 1P) returns HTTP 401 on every TF apply without any signal to the operator that the 1P entry itself needs updating. **Diagnostic**: `curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" https://api.cloudflare.com/client/v4/user/tokens/verify` returns `{"success":true,"result":{"status":"active"}}` for a healthy token. Use this BEFORE running TF after any "CF token might be stale" event (in-session rotation, anniversary alerts, etc.). Recovery sequence after rotation: (a) mint a new token in the CF dashboard with the required scopes, (b) update the 1P item, (c) `homelab-env --refresh` to repopulate `~/.cache/homelab/env.sh`, (d) `/user/tokens/verify` confirms before any TF. Surfaced 2026-05-26 Phase 5j: the 1P value was stale, refresh pulled the same stale value, regenerated value was also invalid (operator-side error replacing the entry) — caused two failed TF planning rounds before discovery.

### DNS

- **Public resolvers as DNS fallback poison internal-zone resolution.** Cloudflare/Google return NXDOMAIN for `*.niflheim.xiiisins.com`; glibc + CoreDNS cache NXDOMAIN as authoritative. Fix in `roles/baseline/tasks/main.yml`: `manage_resolv_conf: false` cloud-init drop-in + templated `/etc/resolv.conf` from `baseline_nameservers`. Fallback resolvers may point at peer AdGuard (`10.0.11.201/202/203`), NEVER public.
- **AGH sync interval `*/30 * * * *` is too long** — 30min lag during keepalived failover serves stale answers. Recommended `*/1`. Sync is cheap + idempotent. Role default is now `*/1` (5b.2, 2026-05-25).
- **`adguardhome-sync` "Sync done" with sub-microsecond duration is NOT trustworthy.** 230ns reads as "no diff" — sometimes accurate, sometimes stale-cached. Diagnose by comparing `/control/rewrite/list` on origin vs replicas directly; if origin has the rewrite but sync reports no diff, restart `adguardhome-sync.service`.
- **K3s `coredns-custom` ConfigMap: `*.override` vs `*.server` keys matter.** K3s's shipped CoreDNS deployment imports `data` keys ending in `*.override` INSIDE the default `.:53` server block (use for plugin directives like `rewrite`, `forward`, `hosts`), and `*.server` keys at TOP LEVEL (use for full new server blocks, e.g. `myzone.:53 { ... }`). Putting a bare plugin directive in a `*.server` key is a syntax error at top level and CoreDNS silently rejects the file — queries fall through to default forwarding and return REFUSED for unmatched zones. Verify the corefile in the cm + the deployment's mount of the custom CM exist before assuming K3s wires it automatically.
- **In-cluster K8s-fronted FQDNs: use a CoreDNS rewrite, not hostAliases.** When a pod needs to reach an internal FQDN (e.g. `authentik.midgard.xiiisins.com`) that AGH resolves to a MetalLB VIP for external clients, the pod→VIP path tromboning kills the connection (same class as the existing "Cloudflared targets ClusterIP DNS, NEVER MetalLB IPs" rule). Cluster-wide fix in `k8s/asgard/infrastructure/coredns-custom/`: `rewrite name exact <fqdn> traefik.traefik.svc.cluster.local` — CoreDNS resolves the rewritten name to Traefik's ClusterIP at query time (ClusterIP-volatility-safe), pod connects to Traefik with the original SNI + Host header, Traefik routes by hostname to the actual backend. External clients (browsers, tailnet devices) unaffected (they bypass CoreDNS, resolve via AGH directly). **Do NOT rewrite FQDNs that point to LXCs/VMs** — those aren't behind Traefik. One rewrite per K8s-fronted internal FQDN; grow the list as more land.

### AdGuard Home

- **AGH admin user is `ghost`, not the role-default `admin`.** Both the `adguard` role's config template (renders `users[0].name`) and the `adguardhome-sync` role's auth (sync→AGH API basic auth) default to `admin` — works for fresh installs but breaks against the migrated manual trio where the operator's username is `ghost`. Override in `ansible/inventory/group_vars/adguard_hosts.yml`: `adguard_admin_user: ghost` + `adguardhome_sync_admin_user: ghost`. The 1P "Adguard - admin" item stores `username: ghost, password: <plaintext>` — `username` is part of the credential, not always `admin`. Symptom of mismatch: sync reports `HTTP 401, username: admin` in journal. Surfaced 2026-05-25 during 5b.2.d.
- **Migrating manual AGH → role-managed: brief dual-MASTER VIP window is unavoidable** when the manual keepalived's `auth_pass` differs from the Vault-minted role one (always true — manual is operator-set, role generates random 8-char). Each side rejects the other's adverts on auth, both think no peer, both claim MASTER. ARP responds twice for the VIP, DNS queries may hit either node. Bounded duration: from "role's keepalived deploys on first non-current-VIP-holder" until "role's keepalived deploys on current-VIP-holder". For the AGH trio that was ~30 min during 5b.2. **No mitigation needed** if Mimir/Kvasir converge BEFORE Saga (current VIP holder), chk_adguard track-script verifies AGH service health on each peer, and AGH is up + serving on at least one node during the window. Eventual election after Saga's role-keepalived deploys is clean (priority 100 wins).

### Postgres

- **PG `hostssl`-only rejects plaintext clients with same error as disallowed CIDR** — `FATAL: no pg_hba.conf entry for host "X", user "Y", database "Z", no encryption`. The "no encryption" suffix is the giveaway. Always set explicit `sslmode=require` on clients; libpq default `prefer` silently downgrades.
- **Patroni 4.x + psycopg3 = mixed-types DataError on config reload.** psycopg3 enforces type homogeneity in list params; Patroni's `_get_pg_settings(changes.keys())` ends up passing a list with both `bytes` and `str` keys → `psycopg.DataError: cannot dump lists of mixed types; got: bytes, str` → Patroni crashes during init before opening REST API. Fix: install with `[etcd3,psycopg2-binary]` extras, NOT `[psycopg3]`. psycopg2 is permissive and the Patroni-recommended driver. `psycopg2-binary` is self-contained — no `libpq-dev` / `build-essential` needed. Surfaced 2026-05-24.
- **`postgres` role's `restart postgresql` handler firing on Patroni-managed nodes fails.** Post-adoption (Fulla) the Debian service is stopped+disabled; on fresh replicas it has no `main` cluster to start. Handler errors either way: `Assertion failed on job for postgresql@17-main.service`. Fix: gate handlers with `when: not postgres_patroni_managed`. Cert renewal in Patroni mode then requires manual `sudo systemctl reload patroni` — once-every-two-years, acceptable. Surfaced 2026-05-24 (Vor first-run TLS cert generation triggered the handler).
- **Patroni's `bootstrap.pg_hba` is initdb-only — invisible on adoption.** `bootstrap.*` only fires when Patroni runs `initdb` (empty data dir). On adoption (existing data_dir, e.g. Fulla), Patroni skips bootstrap → `bootstrap.pg_hba` never applied → leader's pg_hba.conf retains the pre-adoption content (no replication entries). Replicas then fail basebackup: `FATAL: no pg_hba.conf entry for replication connection from host "<replica-ip>", user "replicator"`. Fix: put pg_hba entries at top-level `postgresql.pg_hba` (node-local, applied on every Patroni reload/restart). This means Patroni OWNS pg_hba.conf — re-express anything you need (peer auth, loopback, app CIDRs, per-replica replication rules) in the template. Surfaced 2026-05-24.
- **Patroni adoption preserves the leader's existing `postgresql.conf` as `postgresql.base.conf` and syncs it to replicas during basebackup.** On Fulla, the Debian-stock `postgresql.conf` (with `include_dir = 'conf.d'` at bottom) was preserved as Patroni's `postgresql.base.conf`. Patroni's replica-bootstrap path syncs base.conf from leader to replica. Fulla had `conf.d/` (empty after adoption.yml removed niflheim.conf — harmless no-op); fresh Vor/Idunn never got the dir (`pg_createcluster` was suppressed) → postgres errors: `could not open configuration directory "/etc/postgresql/17/main/conf.d" / FATAL: configuration file ... contains errors`. Fix: ensure empty `/etc/postgresql/<v>/<c>/conf.d/` exists on every PG node (in postgres role's TLS task as `file: state=directory` loop). Surfaced 2026-05-24 on Vor.
- **Patroni `postgresql.pg_hba` needs explicit `host replication <user> 127.0.0.1/32` entries for local pg_rewind.** `host all all 127.0.0.1/32 scram-sha-256` does NOT cover the `replication` pseudo-database — it's a separate `database` match category in pg_hba, not folded into `all`. Without an explicit entry, pg_rewind's local divergence-check fails with `FATAL: no pg_hba.conf entry for replication connection from host "127.0.0.1", user "replicator"` and Patroni falls through to streaming-only catchup. That works for graceful demotions (no divergent WAL) but defeats the `wal_log_hints=on` intent for true-divergence rejoins (ungraceful crash, net-split). Fix: add `host replication <user> 127.0.0.1/32 scram-sha-256` + `::1/128` to the patroni template's `postgresql.pg_hba` list. Plain `host` (not `hostssl`) — loopback doesn't TLS by default; match both encrypted + unencrypted attempts. Surfaced 2026-05-24 during failover validation.
- **`postgres-common` DB provisioning needs the *current* Patroni leader — `--limit fulla` is not safe.** Patroni leadership floats; the DB-creation tasks (`CREATE ROLE`, `CREATE DATABASE`) run via the leader's Unix socket and fail with `cannot execute CREATE ROLE in a read-only transaction` if you hit a replica. **Discovery**: `sudo patronictl -c /etc/patroni/patroni.yml list` from any node — the row marked `Leader running` is the target. **Surgical re-run** for a single DB add: `ansible-playbook playbooks/postgres-host.yml --limit <leader> --skip-tags baseline,postgres,patroni,hardening -e '{"patroni_is_leader": true}'`. The JSON `-e` is mandatory — strict-mode bool conditional rejects `key=value` form. Long-term fix tracked in open-questions: bake leader discovery into the role (query Patroni REST `/cluster` on `:8008` from each node, set `patroni_is_leader` fact). Surfaced 2026-05-26 Phase 5j (Outline DB create attempted on Fulla, which was a replica).
- **Modern Node `pg-connection-string` + `sequelize` treats `sslmode=require` as `verify-full` (rejects self-signed PG certs).** Outline 1.8.0+ crashes at boot with `SequelizeConnectionError: self-signed certificate; if the root CA is installed locally, try running Node.js with --use-system-ca`. The library logs a deprecation warning about this on every connect; `pg-connection-string` v3 + `pg` v9 will fully adopt libpq semantics. **Fix**: `?sslmode=no-verify` in the DATABASE_URL — keeps the TLS envelope (Patroni `hostssl`-only is satisfied) but skips chain + hostname verification. **Class of issue**: any new Node consumer landing on Patroni needs `no-verify` review; libpq-based consumers (Authentik, NetBox, Zabbix, Teamspeak) still accept `require` as the loose mode. Pattern carries until a private CA + cert-manager-issued cert for the Patroni leader replaces the self-signed shape — non-trivial because the leader role floats. Surfaced 2026-05-26 Phase 5j (Outline).

### HAProxy / keepalived

- **VRID collision on shared L2 segment.** Two keepalived clusters on the same L2 (e.g. AGH VIP `10.0.10.200` + PG VIP `10.0.10.210`, both on VLAN 10 HL-ASG-VIP) MUST use different `virtual_router_id`. Same VRID → both receive each other's adverts, auth-fail (different `auth_pass`), log `received an invalid passwd from <peer>` continuously. Allocations as of 2026-05-24: AGH=51, PG=61. **Spacing convention: increment by 10** (next VRRP=71, ...). Allocate during design before deploying a new VIP; mid-flight fix is render + Ansible re-apply + brief re-election.
- **`systemctl kill` doesn't actually kill patroni — and patroni systemd unit ships `KillMode=process`.** Unit defaults to `KillSignal=SIGINT` + `Restart=on-failure`; SIGKILL via `systemctl kill -s SIGKILL` only catches the patroni main PID. PG runs as a child process tree that survives, and `Restart=on-failure` respawns patroni within `RestartSec`. To force an ungraceful crash for failover testing: `systemctl mask patroni && pkill -9 -f patroni; pkill -9 -f postgres` — then `systemctl unmask patroni && systemctl start patroni` to recover.
- **VIP-on-secondary-NIC needs source-based policy routing.** Generalised in the keepalived role via `keepalived_source_policy_routing: [{name, table_id, from, gateway, interface}, ...]` — same class as the K3s workers' VLAN 20 fix (see "Networking / multi-homed workers"), N-entry-capable. Required whenever the VIP's L2 segment differs from the host's default route — without it, replies sourced from the VIP IP exit the default-route interface and stateful firewalls (UCG-Ultra) drop them.
- **Dual-NIC LXC for VRRP + service traffic.** HAProxy/etcd LXCs (Hlin/Eir/Snotra) carry eth0 on VLAN 11 (etcd peer + Patroni REST API + default gateway) + eth1 on VLAN 10 (VIP + VRRP). VRRP requires all peers L2-adjacent on the VIP's segment, so every node needs a real address on the VIP VLAN for advertisement source + diagnostics. Pattern reusable for any future VIP where the L2 segment differs from the service IP.
- **HAProxy `option httpchk` against Patroni REST API for leader detection.** `option httpchk GET /master HTTP/1.0` + `http-check expect status 200` — only the current Patroni leader returns 200 on `/master`; replicas return 503 → HAProxy marks them DOWN. With `balance first` + one-UP-at-a-time, routing is fully deterministic. `on-marked-down shutdown-sessions` (in `default-server`) forces clients to reconnect on demotion — without it, long-lived sessions stick to the deposed primary until they error out, defeating failover transparency.
- **keepalived all-BACKUP election (no static MASTER).** All instances set `state: BACKUP`; election by `priority` only. Avoids dual-MASTER when interfaces come up out of order on simultaneous boot (a static MASTER would announce itself authoritatively before noticing the other peer also announcing).
- **HAProxy + keepalived generic roles — data-driven via list-of-dicts.** `haproxy_listens` and `keepalived_vrrp_instances` are caller-supplied per group; the roles themselves are backend-agnostic and reusable. Adding a new VIP/backend is a group_vars edit, not a role fork. Roles live at `ansible/roles/{haproxy,keepalived}/`; PG-specific config at `ansible/inventory/group_vars/haproxy_etcd_hosts.yml`.

### NetBox

- **`netbox-chart` `existingSecret` references project ALL expected keys without `optional: true`.** Chart docs imply only `secret_key` is needed in the main app secret, but the pod's projected secret volume references `secret_key`, `email_password`, `ldap_bind_password`, `napalm_password` unconditionally — mount hard-fails if any are missing, even for unused features (we don't run email/LDAP/NAPALM). Same for `superuser.existingSecret`: requires `username`, `email`, `password`, `api_token` even though `superuser.username`/`.email` are in the values block (values get ignored when existingSecret is set). Provide all keys as empty strings or non-secret literals where appropriate. Surfaced 2026-05-24 5i.c.
- **`netbox-chart` `extraConfig.secret:` mounts files at `/run/config/extra/<index>/<key>` but the chart's exec-loader doesn't propagate variables to Django settings reliably for index>0.** The `values:` block (always index 0) DOES propagate; secret-mounted files at index 1+ exist on disk but never reach `settings.*`. Symptom: variables defined in the secret file are `<UNSET>` in Django settings even though the file is on disk with correct content. **Fix:** don't use `extraConfig.secret:` — use chart's `extraVolumes` + `extraVolumeMounts` with `subPath` to mount the secret file directly into `/etc/netbox/config/<NN>-name.py` (where NetBox's own loader picks it up). `subPath` is critical — without it, the volume mount replaces the entire `/etc/netbox/config/` dir, clobbering `configuration.py` / `extra.py` / `logging.py` / `plugins.py`. The `NN-` prefix controls load order (NetBox loads `.py` lexically); use `99-` to load LAST so secret values override defaults.
- **`netbox-chart` bundled Valkey defaults to `architecture: replication` (1 primary + N replicas).** For single-tenant cache (reload-on-restart OK), set `valkey.architecture: standalone` — saves disk + complexity. AND `valkey.volumePermissions.enabled: true` is required on Synology CSI iSCSI volumes (same fsGroup-not-honored class as Vault + Authentik Redis); without it, Valkey crashes at startup with `Can't open or create append-only dir appendonlydir: Permission denied`.
- **`netbox-chart` shares ONE RWO media PVC between server + worker.** Synology CSI is iSCSI-only — no RWX. RWO + two consumers means both pods must land on the same node (otherwise the second blocks unmounting from the first). Combined with chart's default anti-affinity, this can deadlock scheduling. **Workaround for first deploy:** `persistence.enabled: false` (emptyDir for media). Acceptable while no device photos exist. If photos start landing, options are (a) accept same-node server+worker scheduling, (b) singleton server-only PVC (worker doesn't actually need media write), (c) deploy a NFS export on Munin for true RWX.
- **`netbox-chart` `resourcesPreset: small` (768Mi limit) OOMs during NetBox migrations.** Django bootstrap + initial migrations + granian worker startup spike well above the `small` preset's memory ceiling. **`medium` (1536Mi) is the realistic floor** for a fresh-install or post-upgrade restart. Documented in the chart's research guide as ~300-500Mi steady-state, but the migration phase has a totally different profile — size for migration peak, not steady state.
- **NetBox 4.6 uses python-social-auth (NOT django-allauth) for OIDC.** Backend class: `social_core.backends.open_id_connect.OpenIdConnectAuth`. Redirect URI must be `https://<host>/oauth/complete/oidc/` with trailing slash mandatory. OIDC endpoint (`SOCIAL_AUTH_OIDC_OIDC_ENDPOINT`) must match the discovery doc's `issuer` value — python-social-auth validates JWT iss claim against it.
- **NetBox 4.6 uses granian (Rust ASGI server), NOT gunicorn.** Doesn't change much in practice, but the "Skipping config initialization (database unavailable)" log on worker startup is granian's per-worker-fork pattern. Cosmetic when it happens once at startup; concerning if it persists during steady-state requests.
- **NetBox runs migrations in the main container entrypoint, NOT a separate pre-install Job.** Means `install.remediation.retries: -1` is critical for first deploy — uninstall-on-failure mid-migration leaves orphan PVCs and partially-migrated DB rows. Restore to standard `3` once the deploy is steady-state (deferred post-flight step). First deploy needs `timeout: 15m` minimum since migrations take 5-10 min on a fresh DB.
- **NetBox OIDC group→permission sync is NOT automatic.** `autoCreateUser: true` provisions a NetBox user record on first OIDC login, but with zero NetBox-side permissions — the user can authenticate but can't do anything. Manually elevate via local-superuser login (`admin` + password from Vault → Admin → Users → check Active/Staff/Superuser). Automating this requires a custom `SOCIAL_AUTH_PIPELINE` override mapping Authentik group claims → NetBox permission assignments — deferred until manual elevation per user becomes recurring toil (>3 users).
- **`API_TOKEN_PEPPERS` must be supplied as `api_token_peppers` key in the netbox-app ExternalSecret** when the chart is run with `existingSecret`. NetBox 4.4+ added pepper-hashed v2 tokens; without peppers configured at startup, NetBox logs "API_TOKEN_PEPPERS is not defined. v2 API tokens cannot be used." and every API token returns `{"detail":"Invalid v1 token"}`. The chart auto-generates the pepper in its `<release>-config` Secret ONLY when no existingSecret is set; with our `existingSecret: netbox-app`, the auto-generation is short-circuited and we have to provide the key ourselves. Value is a JSON-encoded object `{"1":"<random>"}` (single-key supports rotation); loss invalidates every v2 token in the DB. We mint via Vault at `secret/k8s/netbox/api-token-peppers` + back up in 1P "Asgard - NetBox - API token peppers". Fix landed 2026-05-24 commit b576bd2.
- **The chart's `superuser.api_token` value is NEVER inserted as a DB Token row.** The chart stores the value in the `<release>-superuser` K8s Secret as a documentation hint, but `super_user.py` requires THREE things to actually create a Token row: `API_TOKEN_PEPPERS` configured AND `superuser_api_token` AND `superuser_api_key` files mounted (chart only mounts `superuser_api_token`, NOT api_key — separate upstream chart limitation). AND super_user.py exits immediately if the superuser already exists, so post-bootstrap re-runs never retroactively create tokens. **First-token bootstrap:** log in as admin via the web UI (password from `kubectl get secret -n netbox netbox-superuser -o jsonpath='{.data.password}' | base64 -d`) → User menu → API Tokens → Add Token. OR `manage.py shell -c "from users.models import User, Token; Token.objects.create(user=User.objects.get(username='admin'), description='...')"` via kubectl exec. Either way, capture the token value (shown ONCE) and stash in 1P "Asgard - NetBox - admin API token".
- **NetBox 4.6 v2 API tokens use `Authorization: Bearer nbt_<key>.<token>`** (NOT the legacy `Authorization: Token <40-char>` v1 format). NetBox auto-detects v2 via the `nbt_` prefix even when sent under the `Token` keyword. The full secret IS retrievable from `Token.objects.create()` return value's `.token` field — but only at create time; once saved, only the pepper-hashed form is stored. NetBox 4.4+ stores tokens hashed, so a leaked database doesn't expose tokens.
- **NetBox 4.6 User model removed `is_staff`.** Django's standard `is_staff` Boolean attribute was deprecated in favor of NetBox's own object_permissions system. `User.objects.get(...).is_staff` raises `AttributeError`. Affects: the e-breuninger/netbox provider's `netbox_user.staff` field — a no-op against 4.6 (silently accepted, never reflected in DB). `is_superuser` is still there (Django default).
- **Transient OperationalError on `terraform apply` state-refresh.** Django's PG connection occasionally drops mid-request during TF's sequential refresh-GETs — `[GET /dcim/sites/{id}/][500] {"error":"the connection is closed","exception":"OperationalError"}`. Single retry usually succeeds. Likely a Patroni connection-pool blip or Valkey hiccup; not actionable on a one-off. **First diagnostic for any TF apply that 500s on a netbox resource read**: just retry. If it recurs systematically, candidate mitigation is `provider "netbox" { request_timeout = N }` (currently default) or a Django settings override increasing connection retries. Surfaced 2026-05-25 5h.2.b apply.

### Terraform — netbox provider (e-breuninger/netbox v5.3.0)

- **Provider warns "Possibly unsupported Netbox version" for any NetBox >= 4.5.0.** Tested matrix tops out at 4.4.10. Most resources work fine; specific issues below.
- **`netbox_token` is broken against NetBox 4.4+.** The provider's create flow POST + immediately-after PUT errors with `{"error":"Cannot assign a new plaintext value for an existing token."}` — NetBox 4.4+ v2 token API rejects re-sending the plaintext on update; provider unconditionally sends it. Even if create somehow succeeds, the provider's `key` field only captures the 12-char public part (the v2 secret is only in the POST response and the provider has no `token` field in its schema to store it). **Don't use `netbox_token` against 4.4+.** Mint tokens via Django shell or web UI and store out-of-band.
- **`netbox_user` has no `is_superuser` field.** A TF-created user has only `is_active` + `staff` (the latter a NetBox-4.6 no-op). Can't grant the actual permissions the user needs to do anything. For TF → NetBox writes, use the admin token throughout — no dedicated `terraform` NetBox user.
- **`netbox_ip_address`: `device_interface_id` and (`interface_id` + `object_type`) conflict.** Pick one. For device interfaces use `device_interface_id` (simpler — no `object_type` needed). For VM interfaces use `virtual_machine_interface_id`. Don't set both, even if the docs make it look like they're complementary.
- **VM primary-IP binding uses `netbox_primary_ip`**, NOT `netbox_virtual_machine_primary_ip` (which doesn't exist). The device-side equivalent IS `netbox_device_primary_ip`. The two-name asymmetry trips up first-time module-writers.
- **`custom_fields = (Map of String)` cannot write to integer-typed NetBox custom fields.** Provider stringifies all values; NetBox strictly type-checks integer fields and rejects strings with "Value must be an integer." Open upstream issue #349, not fixed in v5.3.0. **Workaround:** define the field as `type = "text"` and enforce numeric discipline TF-side via `validation_regex = "^[0-9]*$"`. Our `VMID` custom field is text for this reason despite being semantically a number — see `terraform/netbox/custom_fields.tf`.
- **NetBox does not allow changing a custom_field's `type` after creation.** `PUT /extras/custom-fields/{id}/` with `type` changed errors `{"type":["Changing the type of custom fields is not supported."]}`. To migrate: destroy + recreate via TF (`terraform apply -replace=netbox_custom_field.X`). The destroy cascade-clears all values on consuming resources; declare the values in HCL so the next apply re-populates from your source of truth.
- **Provider sends `tags = []` and `custom_fields = {}` when the field is unset in HCL** — silently clearing any NetBox-side values that aren't declared. To preserve, you MUST declare the field in HCL (even if just to re-state existing values from the API). `lifecycle.ignore_changes` does NOT suppress this; the provider still sends the field in its PUT body, just from whatever's in state. Read once via API to discover values, then make HCL the source of truth.
- **NetBox slugs auto-strip non-word characters.** `ansible:patroni` → slug `ansiblepatroni` (no `-` insertion; the colon is just dropped). Don't try to predict the slug from the name — read it from `/api/extras/tags/` (or wherever) at retrofit time.
- **Provider's resource list is the authoritative API discovery.** First-line diagnostic for "Invalid resource type" errors: `curl -s 'https://api.github.com/repos/e-breuninger/terraform-provider-netbox/contents/docs/resources?ref=v5.3.0' | jq -r '.[].name'`. Beats guessing from docs that drift across provider versions.

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
- **`ssh_args` MUST live under `[ssh_connection]` in `ansible.cfg`, NOT `[defaults]`.** Placed under `[defaults]` it's silently ignored and Ansible falls back to the built-in default (`-C -o ControlMaster=auto -o ControlPersist=60s`) — your custom args never take effect. Symptom: hosts with hardened sshd (MaxAuthTries=3 from the hardening role) become UNREACHABLE because the SSH client offers every key in `~/.ssh/` before settling on the right one, hitting the limit before the matching key is tried. Ansible's config sectioning is strict — connection-related options live under `[ssh_connection]`, no auto-merge with `[defaults]`. Fix: move `ssh_args` to its own section. Surfaced 2026-05-25 mid-Phase-5b.2 after hardening locked down Mimir.
- **`tasks_from:` is silently ignored in the play-level `roles:` block.** Only `import_role` / `include_role` (as task entries in `tasks:` / `pre_tasks:` / `post_tasks:`) honor `tasks_from`. Using it under `roles:` falls back to `main.yml` with no error or warning. Symptom: a "sandwich" pattern like `roles: [postgres tasks_from: prepare, patroni, postgres tasks_from: bootstrap]` actually runs main.yml twice + patroni in the middle — both invocations re-execute main.yml's full content, and any leader-gated bootstrap conditional in main.yml gets skipped because the gate's never reached the right way. **Fix:** split into separate roles whose `main.yml` does exactly one thing each (the postgres-common refactor at commit `69d72d3` was the response — `postgres` role does prepare-only, `postgres-common` role does the leader-gated user/DB provisioning, playbook lists them sequentially in `roles:`). Or use `import_role` in `tasks:` instead of the `roles:` keyword.
- **`include_tasks` is dynamic — `--tags` doesn't propagate to inner tasks.** Only the include statement itself gets the tag at runtime; tasks inside the included file are loaded later and don't inherit. Symptom: `--tags postgres-common-databases` matches the include in `main.yml`, runs it, but `databases.yml`'s inner tasks all silently no-op (no error, PLAY RECAP shows `ok=1`). Contrast with `import_tasks` (static, tags propagate at parse time). **Workaround for surgical re-runs of a role embedded in a multi-role play**: `--skip-tags <heavy-roles>` against the roles you don't want to re-execute, accept that all of the target role's tasks run. Don't try to use `--tags` to drill into the role from the play level. Alternative: convert the include to `import_tasks` if the included file doesn't need runtime-conditional inclusion.
- **Strict-mode boolean conditionals need JSON `-e`, not `key=value`.** `-e patroni_is_leader=true` produces the string `"true"`, which evaluates truthy in older Ansible but trips strict-mode with `Conditional result (True) was derived from value of type 'str' at "<CLI option '-e'>"`. **Fix:** pass via JSON object `-e '{"patroni_is_leader": true}'` — real boolean, satisfies type-checker. Same applies to lists / dicts / any non-string type. Surfaced 2026-05-25 Phase 5g while overriding `postgres_databases` for a surgical re-run (Zabbix entry blocked the loop because its Vault path doesn't exist yet — see "Backfill TODO" in `terraform/vault/main.tf`).

### LXC / Proxmox

- **bpg/proxmox API token can change `nesting`, NOT other LXC features.** `keyctl`, `fuse`, `device_passthrough` require `root@pam` — `device_passthrough` also at *create*-time. Workaround: aliased provider pattern (`provider "proxmox" { alias = "root", username = "root@pam" }` with `PROXMOX_VE_PASSWORD`; declare `configuration_aliases = [proxmox.root]` in `versions.tf`; resources needing these features use `provider = proxmox.root`). Don't put `keyctl: false`/`fuse: false` in resource block — they're defaults, omitting avoids future 403s.
- **`terraform import` of an existing LXC always proposes destroy-recreate** unless `lifecycle.ignore_changes` is set. The bpg/proxmox provider doesn't return `operating_system.template_file_id` (Proxmox forgets the source template after creation) or `initialization.user_account` (create-only seed) from the API on read, so post-import plan shows both as null-on-read vs the HCL spec's set value — both flagged "forces replacement", which would destroy the running container. Fix: `lifecycle { ignore_changes = [operating_system[0].template_file_id, initialization[0].user_account] }`. Works for fresh creates too — ignore_changes only suppresses diffs, values are still written at creation. Add to any resource being imported. Also include `features[0].keyctl` if the existing container has keyctl=true (community-scripts installer enables it) — switching to the root-aliased provider just to flip keyctl is heavier than the value; ignore the diff. Surfaced 2026-05-25 5b.2.b.
- **Systemd 257 in unprivileged LXC requires `nesting=true`.** Debian 13 systemd uses namespace ops needing CAP_SYS_ADMIN in user-ns. Flag name is misleading: does NOT enable nested containerization — that's preserved by `unprivileged=true`, not `nesting=false`.
- **bpg/proxmox may reboot VMs on apply** due to IP state drift — cluster should survive rolling restarts.
- **Proxmox orphan LVs from a host that died mid-clone.** `qm clone` allocates destination LVs *before* writing VM config. Crash between → stranded LVs, no config to own them. Symptom on next clone at same VM ID: `lvcreate ... already exists`. Diagnosis: `qm list | grep NNNN` (no config) + `lvs | grep vm-NNNN`. Recovery: `lvremove -f /dev/pve/vm-NNNN-cloudinit /dev/pve/vm-NNNN-disk-0` (and matching LVs). If phantom config exists, prefer `qm destroy NNNN --purge`. Class: state surviving outside orchestrator view.

### Tailscale

- **Tailscale auth keys cap at 90 days** — API hard-clamps `expiry` to 7776000s. Closest to "indefinite": `reusable = true` + `recreate_if_invalid = "always"` + `expiry = 7776000`. Set `lifecycle { ignore_changes = [expiry] }` on provider <0.29.0 (we're on 0.28.0) to avoid plan-flap. **Rebuilding a Tailscale-joined LXC 90+ days after initial mint requires `terraform apply` on the tailscale module BEFORE re-running Ansible** — otherwise Vault holds an expired key and `tailscale up` fails.
- **Tailscale `tailnet_key.description` is alphanumeric + spaces + identifier-internal hyphens only.** Rejects parens, em-dashes, slashes — generic error, bisect-the-description to debug. Safe template: `"<hostname> <tag> managed by terraform"`.
- **Tailscale subnet routers + exit nodes need `net.ipv4.ip_forward=1` AND `net.ipv6.conf.all.forwarding=1`.** `tailscale up` succeeds without; control plane's per-route-family relay check flags "cannot relay traffic." `--advertise-exit-node` implicitly advertises both `0.0.0.0/0` AND `::/0` → triggers v6 check regardless of LAN v6. v6 sysctl is kernel-state-declarative — flag set is sufficient even with link-local-only v6. Persisted via `/etc/sysctl.d/99-tailscale.conf` in the tailscale role.
- **Tailscale provider 0.28.0 DNS resource names differ from later versions.** Split-DNS resource is `tailscale_dns_split_nameservers` (one resource per `domain`), NOT `tailscale_dns_split_dns` (map-form, added in later provider versions). The provider also exposes `tailscale_dns_configuration` / `tailscale_dns_nameservers` / `tailscale_dns_preferences` / `tailscale_dns_search_paths`. **First-line diagnostic for "Invalid resource type" errors on any provider:** `terraform providers schema -json | jq '.provider_schemas | to_entries[] | .value.resource_schemas | keys[]'` lists every resource the installed provider actually supports — beats guessing from docs that drift across versions.
- **Tailscale OAuth client scopes are editable in-place via admin UI** (Settings → OAuth clients → edit). Adding a new scope to an existing client does NOT rotate the client_id/client_secret — no Vault rewrite needed. Surfaced 2026-05-24 adding `dns:write` to the existing TF OAuth client for 5e.4.
- **Tailscale DSM 7 package upgrades wipe `configure-host` TUN-permissions state.** Boot-up Task Scheduler task re-applies on next reboot — but package auto-update without reboot silently breaks subnet-router/exit-node. Diagnostic: tailnet admin shows "cannot relay traffic"; `tailscale netcheck` no relay. **Recovery:** `/var/packages/Tailscale/target/bin/tailscale configure-host; synosystemctl restart pkgctl-Tailscale.service` as root. Consider disabling auto-update in DSM Package Center.

### SSH / system

- **Hostkey files can be left zero-byte after hard crash mid-write.** ext4 journal restores inode metadata, not page-cache data. sshd serves from in-memory hostkeys until next restart → `sshd: no hostkeys available -- exiting`. Diagnostic: `ls -la /etc/ssh/ssh_host_*_key` (zero-byte, old mtime); cross-reference `find /etc /var -size 0 -newermt '<window>'`. **Recovery:** `rm` empties first (`ssh-keygen -A` skips existing), then `ssh-keygen -A`, then `systemctl start sshd`. Then `ssh-keygen -R <host>` + `-R <ip>` on control node + operator workstations. Defense pending: baseline asserts hostkey files non-empty.
- **`last reboot` showing multiple "still running" entries = hard crashes.** Only current boot can actually be running — others are boot-without-shutdown records. Useful first-pass diagnostic for "broken since some time ago." Pair with `find -newermt` to localize the crash window. wtmp rolls off via logrotate.
- **Rebuilding an LXC/VM at the same IP triggers SSH hostkey mismatch on operator workstations.** `REMOTE HOST IDENTIFICATION HAS CHANGED!` from `~/.ssh/known_hosts`. Ansible itself is unaffected (`host_key_checking = False` in `ansible.cfg`). **Fix on workstation**: `ssh-keygen -R <ip>` (+ `-R <hostname>` if AdGuard rewrite is in `known_hosts`). Add to the rebuild runbook for any LXC that's being recreated at the same IP. Surfaced 2026-05-25 5h.2 (Hermod replaced the 5aff1dd rolled-back attempt at 10.0.11.22).

### Shell / tooling

- **BSD sed (macOS) does NOT support `\s` regex shorthand.** Patterns like `\s+` or `\s*` match literally `\s` followed by `+`/`*` (zero or more `s` characters in some interpretations), never whitespace. Result is silent no-op: the substitution doesn't fire, the original line passes through verbatim. **Critical risk class for redaction-before-print**: `sed -E 's/(=\s*)".*"/\1"<REDACTED>"/'` looks safe but does nothing on macOS, leaking the value to the transcript. Use `[[:space:]]` instead (portable across BSD + GNU). Surfaced TWICE on 2026-05-25 within 30 min — first against the Proxmox tfvars file (token literal printed → rotation triggered), second against AdGuard config (less harmful, but same class). **Rule:** any redaction regex MUST use `[[:space:]]` or hard-coded literal spaces; `\s` is forbidden in user-facing print pipelines.
- **GNU tar `--strip-components=N` strips slash-separated path components, not directory levels.** Tarballs with leading `./` prefix (common — e.g. `./AdGuardHome/AdGuardHome/file`) have `.` as the first component. `--strip-components=1` removes the `.`, leaving the wrapper directory + file path intact. To strip `./AdGuardHome/`-style wrappers needs `--strip-components=2` for `./`-prefixed tarballs OR `--strip-components=1` for plain `AdGuardHome/`-prefixed. **Robust pattern: extract to tempdir + `find` the binary + `cp -a` to the install dir.** Avoids strip-components math entirely + handles broken state (where the path you'd extract over is a stale file vs dir). Symptom of misuse: files land at `<dest>/<wrapper>/<file>` (`/opt/AdGuardHome/AdGuardHome/AdGuardHome` — binary becomes a directory containing the binary; service fails to start with "Is a directory"). Surfaced 2026-05-25 in the `adguard` role; now uses temp-extract + cp.
- **Ansible `ansible.builtin.unarchive` fails with `[Errno 20] Not a directory`** when `--strip-components=N` renames a tarball directory entry to a destination path that exists as a regular file. The module's post-extraction validation stat()s the original (pre-strip) tarball-listed paths against the destination; with strip-components, those paths don't match where files actually landed. Workaround: drop unarchive, use `command: tar` directly (and the temp-extract pattern above to sidestep the strip-components quirks). Surfaced 2026-05-25.
- **fish heredocs `<<EOF` don't work.** Use pipes or temp files. (See feedback memory `feedback_fish_heredocs.md`.)
- **fish command-substitution collapses newlines when echoed via variable.** `set -l var (cmd)` captures list-of-lines; `echo $var | grep` joins with spaces. Preserve structure: pipe directly (`cmd | grep`), or iterate the list (`for line in $var`).
- **`kubectl exec` warning lines pollute scripted parsing.** Multi-container pods print `Defaulted container "X" out of: ...` to stdout. Always pin with `-c <container>` for scripts.
- **`Bash` tool calls don't inherit the operator's interactive shell PATH.** `/opt/homebrew/bin` (Homebrew on Apple Silicon) is in the shell-rc-loaded PATH but absent in the non-interactive shell `Bash` invokes. `op`, `terraform`, `vault`, `kubectl`, `helm` may all not-found despite being installed. Fix: prefix the command with `PATH="/opt/homebrew/bin:$PATH"` or invoke via absolute path. The `homelab-env` shim cache (`. ~/.cache/homelab/env.sh`) sources fine once PATH points at `op`. Surfaced 2026-05-25 during NetBox token verification.
- **Pipe-induced subshell hides env exports.** `cmd-that-exports | tail` runs the exporting cmd in a subshell — exports never reach the parent. Symptom: `homelab-env --refresh 2>&1 | tail -5` looks like it worked, then `echo $NETBOX_API_TOKEN` is empty. Verify cache via the file directly (`grep -E '^export NAME=' ~/.cache/homelab/env.sh | wc -c`), not via inspecting parent env vars. Surfaced 2026-05-25.
- **Inline redaction must handle bash AND fish syntax — or skip redaction entirely.** `homelab-env` writes both `~/.cache/homelab/env.sh` (bash `export KEY=value`) and `~/.cache/homelab/env.fish` (fish `set -gx KEY 'value'`) — `=` vs space + quoting differ. A single regex matching `KEY=.*` redacts bash but lets the fish line through verbatim, leaking the secret. **Rule:** when verifying secret presence in cache, use length-only checks (`wc -c`, `${#var}`) — never try to inline-redact the value in stream output. If you must show a token's identity, hash it (`shasum`, first 8 chars) or count its length. Surfaced 2026-05-25 — NetBox admin API token leaked verbatim via fish-line miss, rotated by operator.

### Terraform / state

- **`terraform init -migrate-state` is incompatible with `-input=false`.** Migration confirmation is interactive ("Do you want to copy existing state to the new backend?" → "yes"); `-input=false` makes it abort with "Can't ask approval for state migration when interactive input is disabled." Fix: drop `-input=false` for the migration init only (`echo yes | terraform init -migrate-state` works as a one-liner). Restore `-input=false` for subsequent plan/apply where there's no migration prompt. Same applies to any other interactive init prompt (provider-version-mismatch confirmation, etc.).
- **Multi-module state migration pattern.** Per module: (1) add `backend "s3" { bucket = ...; key = "<module-path>/terraform.tfstate"; region = "eu-west-1"; encrypt = true; use_lockfile = true }` to `versions.tf`; (2) bump `required_version >= 1.10.0` (`use_lockfile` is 1.10+); (3) `terraform init -migrate-state` + confirm `yes`; (4) `terraform plan` to verify state still resolves the same resources; (5) move the local `terraform.tfstate` + `.backup` files to a stash dir (`~/tmp/stale/tfstate/<module>/`) rather than deleting — recoverable for ~30d. `terraform/aws/` deliberately stays local-state (chicken-egg with the bucket it creates). Surfaced 2026-05-25.

### vmagent / vmui / VictoriaMetrics dashboards

- **vmagent's default ClusterRole (chart `victoria-metrics-agent` 0.39.0) is missing `nodes/proxy`.** The chart grants `nodes` but NOT the `nodes/proxy` subresource that the apiserver-proxy scrape pattern needs (`__address__: kubernetes.default.svc:443` + `__metrics_path__: /api/v1/nodes/<name>/proxy/metrics[/cadvisor]`). Without it, every kubelet + cAdvisor scrape gets 403 Forbidden → `container_*` metrics never reach VM. **Fix**: `rbac.extraRules` in HelmRelease values granting `nodes/proxy` + `nodes/metrics` (`get list watch`). Surfaced 2026-05-25 — resource-usage dashboard was empty until added.
- **cAdvisor `container_*` metrics (via vmagent's apiserver-proxy scrape) have NO `node` label.** The chart's relabel sets `instance` (and `kubernetes_io_hostname`) to the node name; `node` is empty on every series. `sum by(node) (container_*)` collapses every node into a single empty-label series — symptom is "by-node graphs show only one line." **Use `sum by(instance)`** (or `by(kubernetes_io_hostname)`) instead. KSM metrics (`kube_*`) DO have a `node` label set normally — only cAdvisor cardinality differs. Surfaced 2026-05-25.
- **vmui's predefined-dashboard `unit` field is a label suffix, NOT a formatter.** `app/vmui/.../utils/uplot/helpers.ts::formatTicks` appends `unit` verbatim after `toLocaleString("en-US")`. Values like `"bytes"` / `"binbytes"` / `"decbytes"` do nothing — vmui has no auto-scaling B→KiB→MiB→GiB logic and no Grafana-style unit registry. **Convert in PromQL** (`metric / 1024 / 1024 / 1024`) and set `"unit": "GiB"` as a label. For mixed-magnitude series there's no in-vmui fix — pick a fixed unit per panel (node/namespace memory → GiB, pod-level RSS → MiB) and live with the loss of precision on outliers. Surfaced 2026-05-25 dashboard polish.
- **vmui `customDashboardsPath` is read at startup only.** Updating the mounted ConfigMap doesn't hot-reload — vmsingle pod restart (`kubectl rollout restart sts ...`) needed after dashboard JSON changes. (Same class as the subPath ConfigMap rule in Flux/Helm/Kustomize gotchas, but `customDashboardsPath` mounts the whole dir not a subPath; the issue is the application-side load-once behavior, not kubelet snapshotting.)

### vlagent (off-cluster log shipper)

- **vlagent is CLI-flag-only — no YAML config file mode.** Trying to use `-filelog.config <yaml>` errors `flag provided but not defined`. ALL config is CLI flags: `-remoteWrite.url`, `-fileCollector.glob` (array — pass multiple times), `-fileCollector.extraFields` (array, position-aligned with `-fileCollector.glob`). The `ansible/roles/vlagent/templates/vlagent.service.j2` builds the flag list via Jinja loop over `vlagent_log_inputs`. No yaml template under any circumstances.
- **vlagent's default protobuf protocol matches `/insert/native`, NOT `/insert/jsonline`.** First deploy pointed at `…/insert/jsonline` and VL silently dropped every entry (mismatched format). Switch to `https://logs.niflheim.xiiisins.com/insert/native`. The HTTPRoute's `/insert/*` PathPrefix catches both endpoints so the change is purely URL-side. Native is more efficient + matches vlagent's actual default.
- **Debian 13 trixie ships journald-only — no rsyslog by default.** `/var/log/syslog` exists as a stale empty file from earlier package state; vlagent's filelog input ships nothing. The `vlagent` role installs `rsyslog` + `systemctl enable rsyslog` on Debian apt path so the syslog file actually gets written. Native journald collection in vlagent is upstream-pending ([VictoriaLogs issue #1274](https://github.com/VictoriaMetrics/VictoriaLogs/issues/1274)); rsyslog bridge is the tactical answer.
- **K3s VM hosts can't reach the VL HTTPRoute via Traefik VIP.** Hits the K3s-host-network → MetalLB-VIP class (see Networking gotchas). Override in `group_vars/asgard_k3s.yml` to point at the companion ClusterIP Service `victorialogs-ingest` (`http://10.43.14.105:9428/insert/native`). RHEL `/var/log/messages` instead of Debian `/var/log/syslog` also lives in this group_vars override.
- **`vlagent_log_inputs` schema is `{path, service}`, NOT `{glob, extra_fields}`.** Easy to guess wrong from the README or from the internal CLI flag names (`-fileCollector.glob` + `-fileCollector.extraFields`). `path` is the role-input field name; `service` is a label injected into `extraFields` as `service=<name>`. Wrong shape errors out with `object of type 'dict' has no attribute 'path'` inside the systemd unit template. **Rule**: when consuming an existing role's input schema, read `defaults/main.yml` first (the actual data shape), not just the README (documents intent). Surfaced 2026-05-25 5h.2.

### SFTPGo / Factorio

- **SFTPGo sqlite `data_provider_name` must be absolute** (`/var/lib/sftpgo/sftpgo.db`). Unit's `WorkingDirectory=/etc/sftpgo` makes relative paths FHS-wrong. Create the var-lib dir with sftpgo ownership.
- **Factorio reconcile.timer must NOT auto-start in role.** Background timer races ahead of `initial-install.yml` → `factorio --create` hits `.lock` from already-running service. Pattern: timer `enabled` only in `reconcile.yml`; start explicitly at end of `initial-install.yml`.
- **Factorio install dir needs `chown -R factorio:factorio` after extract.** Factorio writes `.lock` at startup; tarball ownership doesn't match. The reconcile script does this — manual installs need it too.

### Zabbix

- **Zabbix apt-repo deb URL has NO `/release/` segment.** Path is `https://repo.zabbix.com/zabbix/<major>/debian/pool/main/z/zabbix-release/zabbix-release_latest_<major>+debian<N>_all.deb` — the `/release/debian/...` variant 404s (verified against the directory listing 2026-05-25). Same pattern for RHEL/AlmaLinux subtrees: `/zabbix/<major>/<distro>/...`, no `/release/` middleware. Common source of the mistake: confusion with the upstream docs "Stable releases" section header — that's a doc heading, not a URL segment.
- **`no_log: true` on shell tasks blanks BOTH stdout AND stderr from playbook output even on failure.** Correct for tasks that interpolate secrets (PGPASSWORD env, `--password` flags), but turns failures into "task failed with no information." Don't drop `no_log` to debug — **stream the failing operation from the operator workstation instead**, bypassing ansible entirely. Pattern for DB schema loads / migrations against a Patroni-VIP-fronted backend:
  ```fish
  set -lx PGPASSWORD (vault kv get -field=value secret/ansible/<path>)
  ssh ansible@<target-host> "cat /path/to/file.sql.gz" | gunzip | \
    psql -v ON_ERROR_STOP=1 \
         "host=<vip> port=5432 dbname=<db> user=<user> sslmode=require" \
         2>&1 | tail -50
  ```
  Streams the input file from the target host through `gunzip` to the local psql client (which talks to the same VIP the role does). Stderr lands in the operator's terminal where it can be read. Pattern is reusable for ANY `no_log` task involving a file-input → command pipeline against a network-reachable service. Surfaced 2026-05-25 7c.2 deploy.
- **Zabbix factory default admin is `Admin` / `zabbix` — NOT auto-rotated from Vault.** The `server.sql.gz` schema seeds the `users` table with `Admin` + bcrypt(`zabbix`). The `zabbix-server` role's `zabbix_admin_password_vault_path` is the POST-rotation value the operator MUST set via the UI after first login — it's not pushed to the DB by any task today (7c.7 plan: Ansible+API rotation post-deploy). Workflow: log in once as `Admin`/`zabbix`, change password via UI to the Vault-stored value, IaC truth converges. Don't try to log in with the Vault value before the manual rotation — Zabbix will reject + lock the account after 5 attempts.
- **Zabbix 5-attempt lockout → SQL unlock.** Zabbix locks any user after 5 consecutive failed login attempts. Stored in the `users` table; clear via psql against the Patroni VIP:
  ```sql
  UPDATE users SET attempt_failed=0, attempt_clock=0, attempt_ip=''
  WHERE username='Admin';
  ```
  Generic across users + Zabbix 6.0/7.0 LTS as of writing. Pair with the `secret/ansible/postgres/zabbix-password` Vault value for psql auth.
- **Default "Zabbix server" host needs renaming to match the agent's `Hostname=`.** `server.sql.gz` ships a default monitored host literally named `Zabbix server` with `Agent interface: 127.0.0.1:10050`. The `zabbix-agent` role configures local agent2 with `Hostname={{ inventory_hostname }}` (e.g. `hugin`). Hostname mismatch → agent's data is dropped → UI shows "no data" / red. **Fix:** Data collection → Hosts → click `Zabbix server` → set *Host name* to match the agent → Update. The 7c.7 auto-registration action will supersede this for OTHER hosts; the local agent on the Zabbix server itself still needs the manual rename on first deploy unless the role also creates an auto-registration action.
- **Debian's nginx default-site shadows app server blocks for unmatched Host headers.** `nginx` package on Debian ships `/etc/nginx/sites-enabled/default` listening on `*:80 default_server`. Any app's server block that doesn't have `default_server` set will lose Host-header-mismatch traffic to the welcome page. App packages (zabbix-nginx-conf included) install their server blocks via `/etc/nginx/conf.d/` but don't touch `sites-enabled/default` — that's an operator step. **Fix:** remove `/etc/nginx/sites-enabled/default` via Ansible `file: state=absent` + notify `restart nginx`. `sites-available/` left intact so re-enabling is a one-symlink revert. Now applied in `roles/zabbix-server/tasks/web-config.yml` — same fix applies to any nginx-fronted app role.

### Zabbix agent + API automation (7c.8 + Round 2)

- **`community.zabbix` 4.x requires the httpapi connection plugin — env vars are 1.x/2.x legacy and rejected.** Symptom: `Module failed: socket_path must be a value` (httpapi connection wasn't initialized → no socket). Pattern: delegate the module call to an inventory host representing the Zabbix server, override connection at task scope. Example shape:
  ```yaml
  - community.zabbix.zabbix_host: { ... }
    delegate_to: "{{ zabbix_agent_api_delegate }}"
    vars:
      ansible_connection: httpapi
      ansible_network_os: community.zabbix.zabbix
      ansible_httpapi_port: 80
      ansible_httpapi_use_ssl: false
      ansible_user: ansible
      ansible_zabbix_auth_key: "{{ lookup('community.hashi_vault.vault_kv2_get', '...').secret.value }}"
      ansible_zabbix_url_path: ""
  ```
- **`delegate_to: localhost` inherits the play-level `become: true` and triggers sudo on the workstation.** Operator's user has no passwordless sudo → task hangs with `sudo: a password is required`. Combined with `no_log: true` (correct for token-bearing tasks), every host fails with zero diagnostic info. **Fix:** explicit `become: false` on delegated tasks. The control-node-side API call has no need for root. **Debug recipe when no_log is masking errors:** `ANSIBLE_NO_LOG=False ansible-playbook ... --limit <one-host>` shows the real error.
- **Zabbix host.create / hostgroup.create APIs don't handle the default 5 parallel ansible forks cleanly.** Roughly 1/5 succeed, the rest fail with the underlying error masked by `no_log: true`. **Fix:** `throttle: 1` on the offending task scopes to one concurrent execution across the play while leaving the rest of the playbook at full parallelism.
- **Zabbix "Admin role" doesn't grant global host visibility — system automation needs "Super admin role".** Admin grants admin-level perms only WITHIN host groups the user has been explicitly granted access to (via user-group → host-group mappings). With no usrgrps assigned and Admin role, the user can authenticate but `host.get` returns empty + `host.create` fails with permission denied. Assign **Super admin role** for any user owning an API token used by system automation.
- **Module is `community.zabbix.zabbix_group`, NOT `zabbix_hostgroup`** despite the parameter being `host_groups`. ansible-doc disambiguates; the bare guess from the Zabbix UI term gets "couldn't resolve module/action."
- **`set_fact` with `run_once + delegate_to: localhost` scopes the fact to localhost only.** Target hosts don't see it; subsequent task interpolation of `{{ <fact> }}` errors with "is undefined." **Fix:** inline the lookup directly in the task's `vars:` block. `lookup()` always runs on the controller regardless of delegation; no per-host fact pre-population needed.
- **Pre-tasks (e.g. host-groups bootstrap) need `group_vars/all/`, not role defaults.** Pre-tasks run BEFORE the role is loaded, so role-default vars aren't in hostvars yet. Symptom: `'zabbix_agent_api_delegate' is undefined` on the bootstrap play. **Fix:** API connection vars (delegate host, port, ssl, user, token vault path) live in `group_vars/all/zabbix.yml`; the role still declares the same keys as documentation + standalone-use fallback. group_vars/all wins per Ansible's variable precedence.
- **`include_tasks` doesn't propagate `--tags` to inner tasks** without `apply: tags:` — already documented above (this session reinforced it). Caught during the zbx_monitor provisioning re-run: include statement ran (loaded users.yml) but inner postgresql_user task silently no-op'd because it didn't inherit the tag. **Fix:** `apply: tags: [...]` on the include. Applied to `postgres-common/tasks/main.yml`.
- **agent2 plugin packages ship separately on Debian** (and similarly named on RHEL). Base `zabbix-agent2` package doesn't bundle the PG/Redis/MySQL/Oracle/etc. plugin binaries. Without `zabbix-agent2-plugin-postgresql`, PG items return `Unknown metric pgsql.<…>` in unsupported state. Override `zabbix_agent_packages` in per-group group_vars (e.g. `postgres_hosts.yml` adds the plugin package). Same applies to Redis/MySQL/Memcached/Oracle templates — each needs its own plugin package.
- **`community.postgresql.postgresql_user` has NO `groups` arg** despite the parameter name suggesting it. To grant predefined-role membership (e.g. `pg_monitor` for the Zabbix monitoring user), follow with a separate `community.postgresql.postgresql_membership` task. `subelements('groups', skip_missing=True)` lets the membership loop iterate (user, group) pairs while users that don't declare `groups` pass through unchanged.
- **`bpg/proxmox_virtual_environment_user_token.value` returns the FULL `USER@REALM!TOKENNAME=UUID` string** (designed for direct use as `Authorization: PVEAPIToken=<value>`). Passing the full string into the Zabbix `{$PVE.TOKEN.SECRET}` macro produces `Authorization: PVEAPIToken=USER@REALM!TOKENNAME=USER@REALM!TOKENNAME=UUID` → 401 on every scrape. **Fix:** `element(reverse(split("=", value)), 0)` extracts just the UUID (safe because the token_id portion contains no `=` and the UUID is hex+dashes).
- **PG 17 split `pg_stat_bgwriter` into `pg_stat_bgwriter` + `pg_stat_checkpointer`** views. The `PostgreSQL by Zabbix agent 2` template targets the older single-view shape; `pgsql.bgwriter` master item returns `Unknown metric` on PG 17. Upstream issue, not actionable in IaC. 6 items unsupported per PG cluster (master + dependents × 3 hosts). Cosmetic; the rest of the template still works.
- **Shell-variable interpolation through `ansible -a` is a secret leak vector.** `PW=$(vault kv get ...); ansible host -a "cmd --secret \"$PW\""` looks safe (inline fetch into local shell var) but ansible's adhoc module echoes the command back in its output with the substituted value, leaking the secret to the transcript. Verified twice in 7c.8 work — zbx_monitor PG password leaked via `zabbix_agent2 -t pgsql.ping[...]`. Rotated via TF `-replace=random_password.<name>`. **Rule:** avoid local shell variable interpolation when the value is sensitive. Use `community.hashi_vault.vault_kv2_get` lookups in a real task with `no_log: true`, or stream a Vault lookup into the remote command server-side.
- **Zabbix unsupported items retry at a 10-minute cadence by default.** Items that recover (e.g. after a missing plugin gets installed) don't immediately flip to OK — they wait for the next retry. To force immediate recheck via API:
  ```json
  {"jsonrpc":"2.0","method":"task.create",
   "params":[{"type":6,"request":{"itemid":"..."}}],"id":1}
  ```
  Type 6 = "Check now." Use this when iterating; otherwise just wait.
- **`Etcd by HTTP` template is server-side (item type 19), NOT agent-side.** Zabbix server (hugin) fetches `{$ETCD.SCHEME}://{$ETCD.HOST}:{$ETCD.PORT}/metrics` directly. With our HTTP-only etcd config on VLAN 11 (no client TLS), server-side scrape Just Works. `{$ETCD.HOST}` resolves to `{{ ansible_host }}` per host so the server hits the right node each scrape. If etcd were TLS-only, this template would need cert handling that gets complex; the homelab's HTTP-only etcd makes it trivial.

### Apprise / AppriseAPI (Hermod, 5h.2)

- **Apprise YAML schema — URL is the dict KEY, not a `url:` field.** Load-bearing rule that silently fails when wrong: every URL in the `urls:` list is a dict KEY, with options (incl. `tag`) nested under it as a list of single-key dicts. URL-as-string-with-sibling-fields is rejected with the cryptic chain `Ignored entry url found under urls, entry #N` → `Unsupported URL, entry #N` → `Failed to load Apprise configuration from memory://` → `There are no service(s) to notify`. AppriseAPI returns HTTP 424 to the producer. **Two valid shapes**:
  ```yaml
  # Tagged routing — URL is dict key, tag-options nested as list-of-single-key-dicts
  urls:
    - discord://id/token/?format=markdown&username=Hrist:
        - tag: critical
    # Untagged fallback — bare URL string, no nested options. Matches notifications
    # with no `tag` field. POSTs tagged critical/alert/media don't also land here.
    - discord://id/token/?format=markdown&username=Hel
  ```
  **Wrong shape that silently breaks**:
  ```yaml
  urls:
    - url: discord://...    # ← rejected ("Ignored entry url found under urls")
      tag: critical         # ← rejected ("Ignored entry tag found under urls")
  ```
  Surfaced 2026-05-25 5h.2 first-deploy after initial Vault values were correct; cost one playbook iteration before the cause was visible in `journalctl -u apprise-api`. Design doc + role template both updated.
- **AppriseAPI config-key in `/notify/<key>` URI leaks via access logs.** Both Caddy's JSON access log and gunicorn's access log echo the full request URI verbatim. Both ship to VictoriaLogs via vlagent → the soft-auth config-key is queryable in VL. **The Caddy IP allowlist is the *only* real access gate**; the config-key buys nothing additional once logs reach a queryable system. For homelab posture (VL is Authentik-gated), this is acceptable — anyone with VL access is already inside the trust boundary. Mitigations exist (Caddy log redaction filter, gunicorn `--access-logformat` override stripping the path) but aren't justified by the current threat model. **Revisit if** Hermod ever gets fronted publicly (Cloudflared) or VL access scope changes; proper fix at that point is HTTP Basic via Caddy's `basicauth` matcher or HMAC-signed payloads, not the URI key.

### Caddy reverse-proxy role

- **`/var/log/caddy/` ownership must be recursive — pre-existing root-owned files inside break startup.** Caddy runs as the `caddy` user from the apt package; if a stale root-owned file lives in the log dir (e.g. from a transient `caddy validate` run-as-root, or any prior manual invocation), Caddy fails at startup with `opening log writer ... open /var/log/caddy/<name>.log: permission denied`. Ansible's `file: state=directory` chowns the directory but NOT files inside. **Fix in the role**: `recurse: true` on the log-dir task. Generic class — applies to any role chowning a log dir as a non-root daemon user. Surfaced 2026-05-25 5h.2.
- **Cloudsmith stable repo rolls forward + drops older versions.** Pin chosen at role-write time (2.10.0 in the initial Hermod 5h.2 design) was already gone from the candidate list by deploy time (current was 2.11.3). Apt task `name: "caddy={{ ver }}*"` with trailing wildcard does NOT reliably constrain (observed: first run installed 2.11.3 anyway; second run with same role wanted "2.10.0" → "Packages were downgraded and -y was used without --allow-downgrades"). **Fix**: pin without wildcard (`name: "caddy={{ ver }}"`, exact match) + `allow_downgrade: true` on the apt task for pin-resilience. When bumping pins for any rolling apt repo, verify the target version is still in the candidate list first (`apt-cache policy <pkg>`). Same class applies to Debian backports + any third-party CI mirror.

### Outline (wiki, 5j)

- **Outline image is `outlinewiki/outline` on Docker Hub — `ghcr.io/outline/outline` 403s anonymously.** Common copy-paste error; pod ends up in `ImagePullBackOff` with `failed to authorize: failed to fetch anonymous token ... 403 Forbidden`. Verify image path + current tags at https://hub.docker.com/r/outlinewiki/outline/tags when bumping. Tags follow `<version>-<rebuild>` (`1.8.0-1` = latest 1.8.0 rebuild). The GHCR confusion exists because Outline's *source* repo is on GitHub at `outline/outline`, but official container images publish only to Docker Hub.
- **Outline uses Recreate strategy, not RollingUpdate — migrations run on container entrypoint.** Outline runs `yarn db:migrate` automatically on container start (no separate migration Job). Two replicas would race; the loser crashes and re-rolls. `strategy.type: Recreate` makes migrations serial. Tradeoff: ~30s downtime on every pod restart. Acceptable for homelab single-tenant; revisit if Outline becomes critical-path.
- **Outline OIDC redirect URI lives at `/auth/oidc.callback`** — distinct from python-social-auth's `/oauth/complete/oidc/` (NetBox) and the more common `/oauth/callback`. Authentik provider must register the exact full path: `https://wiki.<host>/auth/oidc.callback`. Register one per hostname the app is served at (Outline reads the `URL` env var for the canonical redirect, but per-hostname registrations cover edge cases like the operator clicking "Sign in" while on the LAN hostname). OIDC discovery path: `<OIDC_ISSUER_URL>/.well-known/openid-configuration` (trailing slash on `OIDC_ISSUER_URL` is mandatory — Authentik's per-app discovery lives at `/application/o/<slug>/`).
- **One Redis per consumer (homelab convention).** Mirrors the Authentik Redis pattern. Hand-rolled single-replica StatefulSet in the same namespace, password from the shared `outline-redis-secret` Secret via secretKeyRef. Keeps blast radius tight (Outline can't see Authentik's session keys, vice versa) and avoids cross-app cache eviction surprises. The cost (one extra pod per consumer) is negligible vs the shared-Redis operational surface (eviction policy, cross-tenancy, restart blast).

- Terraform provider: `bpg/proxmox`
- All IPs static
- **Concrete-pin all IaC versions** — Helm charts, Terraform providers, Ansible role versions. No floats (`~> 4.0`, `0.x`, `2.x`). Renovate deferred until stable state. Known pending tighten: `terraform/vault/` `~> 4.0`. (decisions: "Helm chart pin policy", "IaC pin policy")
- Conventional commits
- Norse mythology naming throughout
- **File-path header** — every source file starts with a comment containing its repo-relative path (e.g. `# k8s/asgard/apps/apex-static/configmap.yaml`, or `<!-- ... -->` for markdown). Lets `grep` find moved files after `git mv`. Applies to `.tf`, `.yaml`, `.yml`, `.j2`, `.sh`, `.fish`, `.py`, `.md`. Retroactive sweep pending.
- Never commit secrets
- **Terraform state never committed.** `*.tfstate`, `*.tfstate.backup`, `.terraform/` gitignored across every `terraform/*/`. State contains decrypted secrets + identity material — committing inverts every Vault/1P discipline. **Remote backend live (2026-05-25):** S3 bucket `xiiisins-homelab-tfstate` (eu-west-1) with native Terraform 1.10+ `use_lockfile = true` locking, key-per-module mirroring repo tree (`<module-path>/terraform.tfstate`). `terraform/aws/` (the bootstrap module that creates the bucket + IAM user) stays local-state by chicken-egg necessity. (decisions: "Terraform state backend — S3 with native locking")
- Shell: fish (heredocs `<<EOF` don't work — use pipes or temp files)

---

## Reference

### Hardware

| Host | CPU | RAM | Disk | Norse name |
|------|-----|-----|------|------------|
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Lexar NM790 (Gen 4, DRAM-less, HMB 3.0) | **Urd** |
| MSI Cubi (NUC-class) | i3-1215u (6c/8t, 2 P-cores) | 32 GB DDR4 | 1TB NVMe Samsung 970 EVO (Gen 3, DRAM) | **Verd** |
| Beelink MINI-S12 | N100 | 16 GB | 512GB NVMe SK Hynix PC300 (Gen 3, DRAM) | **Skuld** |
| Synology DS223J | — | — | 3.5TB RAID1 | **Munin** |

All 1 GbE. No 2.5 GbE planned.

**Storage tier — etcd fsync consistency: Verd ≈ Skuld > Urd.** Urd's NVMe is DRAM-less HMB Gen 4 (slowest under sustained sync); Verd + Skuld DRAM-equipped Gen 3. All three well within etcd's tolerance, massive improvement over the old Urd mSATA.

Urd and Verd are now identical hardware (Cubi i3-1215u / 32GB) after Phase 4c. Skuld is the N100/16GB outlier. Urd long-term plan: dedicated Jellyfin LXC with Intel QuickSync passthrough (i3-1215u UHD Graphics ≫ N5095 UHD).

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
