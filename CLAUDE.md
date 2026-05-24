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
- **DNS: AdGuard Home, NOT Pi-hole.** Three LXCs (Saga/Mimir/Kvasir), keepalived VIP `10.0.10.200`.
- **DNS zones: three-zone scheme.** `xiiisins.com` (apex, external, Cloudflare-resolved) / `midgard.xiiisins.com` (internal alias for publicly-reachable services, AdGuard) / `niflheim.xiiisins.com` (internal-only, AdGuard). Each zone gets its own wildcard cert via cert-manager DNS-01 against the same zone-scoped Cloudflare token (`secret/k8s/cert-manager/cloudflare`, scope `Zone:DNS:Edit + Zone:Zone:Read` on `xiiisins.com`). (decisions: "DNS — three-zone scheme", "External TLS posture")
- **MetalLB: L2 mode.** Workers are multi-homed (eth0 VLAN 21 / eth1 VLAN 20, eth1 IPs `10.0.20.201/202/203` outside the pool). L2Advertisement uses `nodeSelectors` excluding CP nodes (CPs have no eth1). Four landmine fixes required in IaC — see Known gotchas "Networking / multi-homed workers".
- **Internet exposure: KPN Experia Box → UCG-Ultra DMZ.** UCG is the sole firewall policy boundary. UCG posture: `Internal → Any: Allow`, `External → Internal: Allow Return`, `Any → Any: Deny` (last). Port-forwards on UCG only. KPN is never in IaC — changes recorded in docs or they don't exist.

### Storage / data
- **Storage: Synology CSI driver (christian-schlichtherle/synology-csi-chart), iSCSI only.** Single StorageClass `synology-csi-iscsi-retain` (default). NFS creates polluting shared folders on Synology. Not democratic-csi. One CSI instance per K3s cluster.
- **iSCSI LUNs are per-PVC.** Synology CSI creates one target+LUN per PVC (`iqn.2000-01.com.synology:munin.pvc-<uuid>`). Vestigial target `iqn.2000-01.com.synology:munin.k3s-core.f954439fc46` from an abandoned NFS-CSI attempt is NOT in use.
- **VM disks: local LVM-thin.** Faster than NFS at 1 GbE.
- **No Galera. PostgreSQL only.** Zabbix migrated to PostgreSQL.
- **PG backend: local LVM-thin** (not NFS). NFS fsync semantics + WAL latency over 1 GbE are anti-patterns. (decisions: "PG storage backend")
- **PG HA via Patroni** (5g.2 in flight). etcd DCS co-located on HAProxy trio (Hlin/Eir/Snotra), NOT on PG nodes. No pgbouncer (revisit triggers documented). Single HAProxy VIP `10.0.10.210` for all consumers. (decisions: "PG HA management — Patroni", "Patroni DCS placement", "pgbouncer in the connection chain", "PG consumer connection model")

### Services / placement
- **Jellyfin: privileged LXC on Urd.** Intel QuickSync `/dev/dri` passthrough. Not in K3s.
- **Monitoring: Zabbix LXC (outside K3s) + VictoriaMetrics/Logs/Grafana in jotunheim K3s.** Zabbix stays LXC for monitoring independence. VictoriaLogs replaces Loki; VictoriaMetrics replaces Prometheus.
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
- **LXC management: Terraform `asgard-lxcs` module** (`terraform/proxmox/asgard-lxcs/`). Same provider + tfvars as `asgard-k3s/`. Add new LXC: append to `lxcs.tf` → `terraform apply` → add to `inventory/hosts.yml` → write role + playbook.
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

**Workers (Einherjar-urd/verd/skuld)** — 2 vCPU / 4GB / 15GB disk, eth0 on VLAN 21, eth1 on VLAN 20 (MetalLB L2 — IPs `10.0.20.201/202/203`), RHEL 9 + K3s + Calico + iscsiadm.

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
- ✅ Factorio LXC (1120) — operator self-service via SFTPGo, reconcile loop (2026-05-16)
- ✅ Fulla / PostgreSQL 1 (LXC 1130) — PG 17, TLS, scram-sha-256, two SUPERUSER management roles in Vault (2026-05-17). Standalone; cluster expansion in flight via Phase 5g.2.
- ✅ Authentik + Redis — Authentik 2026.2.3 + hand-rolled Redis StatefulSet (2026-05-17). Now exposed via Traefik+Gateway+Cloudflared.

**Pending:**
- 🟡 AdGuard Home — functionally ✅ (Saga/Mimir/Kvasir, VIP 10.0.10.200, sync) but **manually installed, no Ansible role**. Sync bumped `*/30` → `*/1` cron. Phase 5b.2 — AdGuard IaC — pending (Terraform LXC + Ansible roles).
- 🟡 Phase 5g.2 — PG HA with Patroni. Done: design + decision rows (2026-05-24), HAProxy/etcd trio + Vör/Idunn PG LXCs provisioned + baseline (1131-1135), etcd 3-node cluster bootstrapped on Hlin/Eir/Snotra (Snotra leader), **Patroni cluster `niflheim-pg` live 2026-05-24 (3/3 streaming on TL 1: fulla Leader, vor + idunn Replicas, 0 lag)**. Remaining: HAProxy + keepalived VIP `10.0.10.210` → Authentik cutover from IP-stopgap → failover validation. Deferred to the cutover step: orchestrated `patronictl restart niflheim-pg` to apply 4 pending params on fulla (`max_connections 100→300`, `wal_log_hints off→on` for `pg_rewind`, `cluster_name`, `listen_addresses`). Step-list in [`docs/operations/open-questions.md`](docs/operations/open-questions.md).
- 🔲 Remaining asgard LXCs (Teamspeak, Zabbix, Jellyfin)
- 🔲 Jotunheim K3s
- 🔲 Services (Outline, Immich, Grafana, VictoriaMetrics, VictoriaLogs, Netbox, n8n, Privatebin, Startpage)

---

## Known gotchas

*Incident retrospectives in [`docs/incidents/`](docs/incidents/). These entries are the rules + recovery commands.*

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
- **Patroni 4.x + psycopg3 = mixed-types DataError on config reload.** psycopg3 enforces type homogeneity in list params; Patroni's `_get_pg_settings(changes.keys())` ends up passing a list with both `bytes` and `str` keys → `psycopg.DataError: cannot dump lists of mixed types; got: bytes, str` → Patroni crashes during init before opening REST API. Fix: install with `[etcd3,psycopg2-binary]` extras, NOT `[psycopg3]`. psycopg2 is permissive and the Patroni-recommended driver. `psycopg2-binary` is self-contained — no `libpq-dev` / `build-essential` needed. Surfaced 2026-05-24.
- **`postgres` role's `restart postgresql` handler firing on Patroni-managed nodes fails.** Post-adoption (Fulla) the Debian service is stopped+disabled; on fresh replicas it has no `main` cluster to start. Handler errors either way: `Assertion failed on job for postgresql@17-main.service`. Fix: gate handlers with `when: not postgres_patroni_managed`. Cert renewal in Patroni mode then requires manual `sudo systemctl reload patroni` — once-every-two-years, acceptable. Surfaced 2026-05-24 (Vor first-run TLS cert generation triggered the handler).
- **Patroni's `bootstrap.pg_hba` is initdb-only — invisible on adoption.** `bootstrap.*` only fires when Patroni runs `initdb` (empty data dir). On adoption (existing data_dir, e.g. Fulla), Patroni skips bootstrap → `bootstrap.pg_hba` never applied → leader's pg_hba.conf retains the pre-adoption content (no replication entries). Replicas then fail basebackup: `FATAL: no pg_hba.conf entry for replication connection from host "<replica-ip>", user "replicator"`. Fix: put pg_hba entries at top-level `postgresql.pg_hba` (node-local, applied on every Patroni reload/restart). This means Patroni OWNS pg_hba.conf — re-express anything you need (peer auth, loopback, app CIDRs, per-replica replication rules) in the template. Surfaced 2026-05-24.
- **Patroni adoption preserves the leader's existing `postgresql.conf` as `postgresql.base.conf` and syncs it to replicas during basebackup.** On Fulla, the Debian-stock `postgresql.conf` (with `include_dir = 'conf.d'` at bottom) was preserved as Patroni's `postgresql.base.conf`. Patroni's replica-bootstrap path syncs base.conf from leader to replica. Fulla had `conf.d/` (empty after adoption.yml removed niflheim.conf — harmless no-op); fresh Vor/Idunn never got the dir (`pg_createcluster` was suppressed) → postgres errors: `could not open configuration directory "/etc/postgresql/17/main/conf.d" / FATAL: configuration file ... contains errors`. Fix: ensure empty `/etc/postgresql/<v>/<c>/conf.d/` exists on every PG node (in postgres role's TLS task as `file: state=directory` loop). Surfaced 2026-05-24 on Vor.

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

## Conventions

- Terraform provider: `bpg/proxmox`
- All IPs static
- **Concrete-pin all IaC versions** — Helm charts, Terraform providers, Ansible role versions. No floats (`~> 4.0`, `0.x`, `2.x`). Renovate deferred until stable state. Known pending tighten: `terraform/vault/` `~> 4.0`. (decisions: "Helm chart pin policy", "IaC pin policy")
- Conventional commits
- Norse mythology naming throughout
- **File-path header** — every source file starts with a comment containing its repo-relative path (e.g. `# k8s/asgard/apps/apex-static/configmap.yaml`, or `<!-- ... -->` for markdown). Lets `grep` find moved files after `git mv`. Applies to `.tf`, `.yaml`, `.yml`, `.j2`, `.sh`, `.fish`, `.py`, `.md`. Retroactive sweep pending.
- Never commit secrets
- **Terraform state never committed.** `*.tfstate`, `*.tfstate.backup`, `.terraform/` gitignored across every `terraform/*/`. State contains decrypted secrets + identity material — committing inverts every Vault/1P discipline. Local on operator workstation. Long-term: remote backend (Vault KV-backed) once justified.
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
- `10.0.10.200` AdGuard VIP / `10.0.10.210` PG HAProxy VIP (planned)
- `10.0.11.20` PBS / `10.0.11.201/202/203` AGH (Saga/Mimir/Kvasir)
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
| DNS zones | `xiiisins.com` (apex, external) / `midgard.xiiisins.com` (internal alias) / `niflheim.xiiisins.com` (internal-only) |

### Repo map

```
homelab/
├── CLAUDE.md
├── docs/                                  # See docs/homelab-design.md for the index
├── terraform/
│   ├── proxmox/{asgard-k3s,asgard-lxcs}/  # VM + LXC definitions (bpg/proxmox)
│   ├── vault/                             # Vault config (KV, auth methods, policies, roles)
│   ├── cloudflare/                        # Tunnel + DNS records + Vault KV writes
│   ├── authentik/                         # OIDC providers + identity-as-data (users.yaml + groups.yaml)
│   ├── tailscale/                         # Tailnet ACL (policy.hujson) + auth keys
│   └── {dns,aws,k3s}/                     # Scaffolding (empty)
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
