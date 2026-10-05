<!-- docs/operations/aiops-roadmap.md -->

# Phase 10 — AIOps & self-healing roadmap

*Planning document, drafted 2026-10-01. Status: 🟡 in progress — **10a-10e done and live; 10f deployed 2026-10-03, canary soak running (read-out about 2026-10-17); 10g stage A proven 2026-10-04; 10h live in all three parts, exit criteria not yet met** (live state checked 2026-10-05, see [Live state](#live-state-checked-2026-10-05)); the restore drill passed 2026-10-03; the 10a soak ends 2026-10-08. Phase rows live in [`build-sequence.md`](build-sequence.md) (Phase 10, 10a–10i); decisions in [`decisions.md`](decisions.md); prerequisite debt in [`open-questions.md`](open-questions.md).*

---

## Goal

Close the loop between **detection** and **action** so the fleet converges and recovers without the operator, in stages of increasing autonomy — and never faster than the guardrails behind each stage can be proven.

The directional intent is already on record: drift converges automatically, and dead or misbehaving VMs/LXCs are torn down and rebuilt from the repo (extending the Patroni/Flux posture to the fleet layer). Redundancy stays an **application-level** concern (Vault Raft, Patroni, K3s etcd) — not storage-level, not "accept the loss". Today that direction is operator-driven: Semaphore's drift-check is read-only and the only closed loop is `frigg-reauth-listener`.

**Non-goals**
- An LLM with free shell. The agent acts **only** through a registry of named, idempotent, tiered actions executed by Semaphore.
- Autonomous action on quorum members, the control plane, or anything stateful (see [Blast-radius tiers](#blast-radius-tiers)).
- A second always-on bare-metal site. Off-site capacity is DigitalOcean (cheap, disposable); AWS stays KMS + state + cold copies.
- Making the offsite a quorum member. It cannot protect against site loss (a majority has to be where the users are); it is for DR copies, an outside watcher, and burst testing.

---

## Starting point (2026-10-01)

| Layer | What exists |
|---|---|
| Desired state | Git → Flux (K8s), Terraform, Ansible (NetBox dynamic inventory), NetBox as queryable view |
| Detection | Zabbix (host/LXC, Hugin), VictoriaMetrics/Logs + vmagent/vlagent, S4 infra-health prober (Semaphore-scheduled) |
| Reconciliation | Semaphore: `drift-check` every 6h (read-only), `asgard-apply` on demand, `platform-version-drift` (check-mode) |
| Notification | Hermod (Apprise) — response-time severity tags (`critical` = look within minutes) → Discord |
| Agent host | Frigg (HA VM 2900, Vault-backed shim, `claude remote-control` as systemd, remote-host Ansible); `frigg-reauth-listener` self-heals RC login invalidation |
| Upgrade agent | `chart-bump` (investigate → worktree → render-diff → commit → Flux → tests → docs) — the model for agent-authored change |
| Known weak spots | Skuld hard-freezes (watchdog unproven); PBS co-located on Skuld and its datastore at 85% (75% on 2026-10-03; large consumers aging out); no offsite copy of Calico datastore / etcd snapshots; RAM-tight Proxmox hosts |

---

## Principles & guardrails

1. **Registry, not improvisation.** Every action the agent can take is a named entry (Semaphore template + typed extra-vars + tier + guard + verify step). No ad-hoc shell in autonomous mode.
2. **Classify the fault layer before acting.** Host-level vs workload-level. The 2026-09-30 Skuld freeze looked like a bad chart bump (Helm timeouts, `Terminating` pods) and the Immich rollback then poisoned its own DB — the agent must say "dead host", not "bad release", before touching anything.
3. **Blast-radius tiers** (T0–T3, below) bound what may run unattended.
4. **Kill switch before autonomy.** One flag (Vault KV + a Discord command) stops all autonomous action; it ships in 10f1, before any T1 class goes live.
5. **Idempotency guards.** An action is automatable only if replaying it is provably safe. The 2026-10-01 Calico datastore-prune incident is the cautionary tale — every automatable role/playbook needs a guard like `calico-upgrade.yml`'s datastore export + CRD-prune check.
6. **Least privilege per stage.** The diagnosis session has **no write credentials**. The executor key can trigger only allow-listed Semaphore templates. Agent identity is separate from the operator's admin tokens.
7. **Audit everything.** Alert → diagnosis → proposal → approval → execution → verification, with the alert fingerprint as the join key (VictoriaLogs + NetBox journal).
8. **Rate-limit, circuit-break, verify.** N attempts/hour/target, then escalate; the originating alert must clear after the action or it rolls back / escalates. Flapping must not become a loop.
9. **Test on disposable capacity first.** Canaries (Urd), then redundant replicas, then the rest; DO burst droplets for anything destructive. Never prototype heal loops on quorum members.
10. **Maintenance windows suppress autonomy.** Patching playbooks (`proxmox-host-patching.yml`, `k3s-upgrade.yml`, `calico-upgrade.yml`) set a flag the loop honours.

---

## Blast-radius tiers

| Tier | Meaning | Examples | Autonomy |
|---|---|---|---|
| **T0** | Read-only | diagnosis, queries, forecasts | always |
| **T1** | Stateless / replicated / rebuildable from IaC with no data loss | AdGuard replicas (Mimir/Kvasir), Tailscale LXCs, Hermod, canaries, `do1`, stalled `HelmRelease` resets, known-condition cleanups | auto, notify after (10f) |
| **T2** | Stateful, user-visible, or single-instance | Factorio, workers (until proven), Postgres replicas, Hugin/Zabbix, NetBox, Authentik | approval-gated (10e); some graduate to T1 after soak |
| **T3** | Quorum members, control plane, hypervisors, state stores, the agent host itself | CPs/etcd, Vault, Patroni leader, HAProxy/etcd trio, PBS, PVE hosts, Synology, UCG, Frigg | human only; agent may diagnose |

Frigg is a single control point: if it dies the loop dies. The outside watcher (Gatus on `do1`, 10b3) is what notices that; rebuilding Frigg stays human (T3) until a second control node exists.

---

## Phase map

| Phase | Name | Depends on | Maps to stage |
|---|---|---|---|
| **10a** | Offsite node `do1` via IaC (TS3 failover + PlantNet proxy) | — | enabler |
| **10b** | Test substrate + outside watcher | 10a (provider/Terraform groundwork) | enabler |
| **10c** | Machine-readable ops (alert schema, runbook metadata, action registry) | — | Stage 0 |
| **10d** | Diagnosis-only chat-ops | 10c | Stage 1 |
| **10e** | Approval-gated actions | 10d, 10b1 | Stage 2 |
| **10f** | Autonomous T1 healing | 10e + kill switch + canaries | Stage 3 |
| **10g** | Fleet rebuild loop | 10f + restore-tested backups + PBS off Skuld | Stage 4 |
| **10h** | Predictive & agent-authored change | 10f | Stage 5 |
| **10i** | Pod rightsizing (VPA recommend-only, Gná digest, rightsizing PRs) | 10h1, 10h2 | Stage 5 (applied to K8s requests) |

10a/10b/10c can run in parallel (disjoint scopes). Everything from 10d onward is sequential.

---

## 10a — Offsite node `do1` (IaC rebuild)

**Why:** the existing DigitalOcean droplet is an unmanaged, years-old Docker box (hand-built NPM/Portainer/Kuma, EOL-ish OS, a stale firewall binding, an abandoned OpenTAKServer remnant). It carries two live duties: the **TeamSpeak failover** (SRV priority 99 → `do-ts3`) and **HeyLeaf's PlantNet proxy** (`plantnet.heyleaf.app`; PlantNet allowlists a fixed IPv4 — the reason this is a droplet and not App Platform, where a dedicated egress IP adds ~$25/mo). Wipe-and-rebuild, not patch-in-place; build beside, validate, cut over, destroy.

**Footprint (deliberately minimal — two duties):** `s-1vcpu-1gb` (~$6/mo), Debian 13, ams3, a reserved IP, Docker + Caddy + two containers (`teamspeak`, `plantnet-proxy`) plus the **Gatus outside watcher** as a small systemd binary (10b3). The box serves the failover/proxy and watches the homelab; nothing else. **No** Portainer, Nginx Proxy Manager, Uptime Kuma or other admin UIs.

- **10a1 — Terraform `terraform/digitalocean/`.** Provider with a **new least-privilege token** (Vault, via the shim; the old broad token is revoked at cleanup); project `homelab-offsite`; droplet; reserved IP; **one explicit cloud firewall bound by droplet ID** (see [`known-issues/digitalocean.md`](../known-issues/digitalocean.md): firewalls are additive and tag-bound); SSH keys declared explicitly. In `terraform/tailscale/`: new `tag:offsite` in `policy.hujson` + `tagOwners`, a tagged pre-authorized key minted to Vault (existing `authkeys.tf` pattern) so the node joins tagged from first boot (no key expiry), and **scoped grants replacing the allow-all `* → *`** for this tag. NetBox declaration in `terraform/netbox/vms.tf` (standing TF→NetBox rule). Cloudflare: bring `do-ts3` / `hel-ts3` / the `_ts3._udp` SRV into `terraform/cloudflare/` (overrides the "leave hand-managed" note in [`services/teamspeak.md`](../services/teamspeak.md)).
- **10a2 — Ansible `do1.yml`.** Existing roles: `baseline`, `hardening`, `tailscale`, `os-updates`, `caddy-reverse-proxy`, optionally `vlagent`/`zabbix-agent` over the tailnet. New: `docker` role; compose deployment for `teamspeak` and `plantnet-proxy`. The proxy image is **built on the host from the HeyLeaf repo source** (`plantnet-proxy-docker/`, pinned commit) — there is no registry and no CI; the old image was a local build. PlantNet API key from Vault `secret/ansible/do1/plantnet` (already seeded 2026-10-01; 1P mirror is the operator's). Firewall: 22, 80, 443, 9987/udp, tailscale 41641/udp — TS3 query (10011) and file-transfer/TSDNS (30033/41144) stay closed publicly. **Co-location mitigations** (the box is the internet-facing one *and* hosts the watcher): a `DOCKER-USER` rule drops traffic from the container bridges to the tailnet range, so a compromised proxy container cannot inherit the host's tailnet reach; the `tag:offsite` ACL allows only the Gatus probe/heartbeat ports on Frigg/Hermod (one node = one combined permission set — tags cannot split watcher from proxy); the `gatus` role (10b3) runs as its own unprivileged user; unattended-upgrade reboots run in a fixed window with a matching heartbeat grace period.
- **10a3 — Restore, cutover, cleanup.**
  1. Restore the TS3 SQLite DB from the dump held on Frigg (`~/do1-ts3-dump/`, integrity-checked; taken via the SQLite backup API because the live DB is WAL-mode — a plain file copy misses data).
  2. Operator adds the **new reserved IP** to the PlantNet allowlist (my.plantnet.org) **before** cutover; keep the old IP until validated.
  3. Validate: an end-to-end identify request through the new proxy; TS3 client connects via the SRV fallback with the homelab TS3 stopped briefly.
  4. DNS: xiiisins.com records via Terraform; `plantnet.heyleaf.app` is in a separate Cloudflare zone, **changed manually by the operator**.
  5. Soak ~7 days, then destroy: old droplet, the powered-off `do-tailscale-p01`, both stale firewalls, the unused `startpage` registry and stale SSH keys; delete the pre-hardening snapshot; revoke the old API token.
- **Exit:** TS3 failover + proxy serve from `do1` built entirely by `terraform apply` + `ansible-playbook`; nothing hand-configured; old resources gone; monthly spend ≤ today's.
- **Risks:** PlantNet allowlist lag (mitigated by dual-IP window); HeyLeaf proxy base image `node:18-alpine` is EOL — flag to the HeyLeaf repo, don't fix here; TS3 DB restore fidelity (rehearse on a burst droplet first); the co-located watcher shares fate and attack surface with the public proxy (mitigations in 10a2/10b3; `do1` itself is watched from the homelab so its death is noticed).

---

## 10b — Test substrate + outside watcher

- **10b1 — Canary pool on Urd.** *(Live 2026-10-01: [`procedures/canary-pool.md`](../procedures/canary-pool.md); final IDs 1190-1192, `10.0.11.190-.192`.)* 3 × 512 MB LXCs (`canary-*`, proposed IDs 1190–1192), ≤ ~2 GB total, via the `asgard-lxcs` module + NetBox declaration. **Not Skuld** (freezes would contaminate fault-injection results) and **not Verd** (Frigg lives there, least headroom). Live headroom on 2026-10-01: Urd ~7.7 GB, Verd ~5.6 GB, Skuld ~8.3 GB available (RAM is the only tight resource; CPU and thin-pool disk are plentiful).
- **10b2 — Burst substrate.** *(Live 2026-10-02, smoke-tested; Frigg TTL reaper running — [`procedures/burst-substrate.md`](../procedures/burst-substrate.md). Since 2026-10-05 it is also the homelab's K8s experiment space and the Phase 9 pilot venue, Phase 7 Jotunheim having been dropped. Shape decisions in `decisions.md`; the shared DO token fits as-is.)* Separate Terraform root `terraform/digitalocean-burst/` (own state; never in the `do1` root): ephemeral droplets tagged `tag:burst` joining the tailnet with ACL scoped to Frigg only (no path to prod). **K3s via the existing `k3s` role on plain droplets, not DOKS** — DOKS is not K3s (your invariant) and its node auto-repair would confound heal/rebuild tests. **Cost guard:** a TTL reaper on Frigg (destroys `tag:burst` droplets older than N hours; token from Vault) plus a DO billing alert — a forgotten cluster must not run for a month. **Also hosts the restore drill** for the offsite backups ([`procedures/offsite-backups.md`](../procedures/offsite-backups.md) marks it "pending 10b2"): restore the etcd snapshot, the Calico objects (CRDs → Installation → objects) and the Vault Raft snapshot onto a scratch cluster here, then destroy it.
- **10b3 — Outside watcher: [Gatus](https://github.com/TwiN/gatus) on `do1`.** Declarative YAML (config in git, templated by a new `gatus` Ansible role), run as an **unprivileged systemd binary** — not a container, which keeps the container→tailnet drop rule (10a2) simple; single Go binary, SQLite history.
  - **Probes:** public endpoints (apex/WebFinger, `home.`, `paste.`), TLS-expiry, and tailnet-only checks of Frigg and Hermod.
  - **Dead-man's switch:** a Gatus *external endpoint* with a `heartbeat` interval that Frigg pings; silence alerts (grace window covers `do1`'s own reboot window).
  - **Independent alert channel:** a direct Discord webhook (secret from Vault, mode 0600, readable only by the `gatus` user) — never via Hermod, which lives in the thing being watched. Answers "who watches the watcher".
  - **Metrics:** Gatus exposes Prometheus `/metrics`; vmagent scrapes it over the tailnet into VictoriaMetrics.
  - **Inside view:** no second UI. Zabbix, the S4 prober and VictoriaMetrics already cover inside checks and are pointed at `do1` as well (mutual watching — the homelab notices `do1` dying). An optional Gatus in asgard via Flux is a later add if an internal status page is wanted.
  - **Not Uptime Kuma:** its monitors are click-ops state in a database (not in git, no official declarative path), memory use varies widely, and the old droplet's Kuma sat unhealthy for weeks.
  - **Status 2026-10-01: ✅ code landed (PR), not yet applied.** `roles/gatus` + `roles/gatus-heartbeat`; deploy/verify/operate in [`procedures/offsite-watcher.md`](../procedures/offsite-watcher.md). Deviations from the text above: Gatus publishes no release binaries, so the pinned binary is pulled from the official image's single layer by OCI digest; the Hermod tailnet check is TCP (Hermod 403s non-allowlisted HTTP) and needs `tailscale --accept-routes` on `do1`; the vmagent scrape and a fast homelab-side `do1` alert are open follow-ups (K3s pods cannot reach tailnet addresses).
  - **Split it back out** to its own droplet if it ever needs Docker, outgrows ~100 MB, needs a public status page, or makes `do1` hard to patch (the role is host-agnostic; this is just re-pointing it).
- **Exit:** blocking Frigg's heartbeat raises an alert through the independent path within the configured window; `do1`'s own death is alerted from the homelab side; a burst K3s cluster can be created and reaped by script; canaries are inventoried and destroyable.

---

## 10c — Stage 0: machine-readable ops

*Status 2026-10-01: ✅ code landed (schemas, routing, 20 runbook ids, 8-action registry, playbooks, CI job) — see [`aiops/README.md`](../../aiops/README.md). Not yet live: the seven new Semaphore templates await an operator `terraform apply` in `terraform/semaphore/`. Notes for 10d in [`open-questions.md`](open-questions.md).*

- **10c1 — Alert schema.** Normalise Zabbix / S4 prober / Hermod payloads: `host`, `service`, `severity` (existing response-time tags), `runbook_id`, `fingerprint`/dedupe key, timestamps.
- **10c2 — Runbook metadata.** The recurring entries in [`known-issues/`](../known-issues/) and [`procedures/`](../procedures/) get a stable `runbook_id`, preconditions, `automatable: none|approval|auto`, tier, and a verify command. Start with the ~10 most-recurring.
- **10c3 — Action registry.** A repo-managed registry (named action → Semaphore template + typed extra-vars schema + tier + guard + verify + rollback note). Initial entries (T0/T1): `service-status`, `restart-unit` (one host), `replay-role --limit <host>` (check-mode first), `flux-reconcile` / `flux-reconcile --reset`, `vault-status`, `patroni-status`. T2 entries added in 10e.
- **Exit:** every `critical` alert carries a `runbook_id`; every registry action has a verify step and a tier.

## 10d — Stage 1: diagnosis-only chat-ops

*Detailed implementation plan (drafted 2026-10-02): [`10d-diagnosis-chatops.md`](10d-diagnosis-chatops.md).*

- **10d1 — Direct ingress to n8n.** Each monitoring system (Zabbix first) sends a second, context-rich message **directly to a dedicated n8n agent** alongside its existing Hermod notification; idempotent per fingerprint, grouped per incident, authenticated. Hermod is not on the analysis path.
- **10d2 — Read-only toolbelt.** Scoped, **write-less** credentials: read-only kubectl ServiceAccount, VL/VM query, Zabbix API read, NetBox read, Proxmox audit-only role, Semaphore read.
- **10d3 — Discord UX + diagnosis template.** Thread per alert with correlated logs/metrics, recent commits, matching known-issue, and an explicit **host-vs-workload classification**.
- **Acceptance — incident replays** (each fed to the agent as if live): 2026-09-30 Skuld freeze (must name a dead host, not a bad release); 2026-10-01 Calico datastore prune (must flag K3s per-addon pruning); the etcd raft-drop syslog flood (disk-fill on surviving CPs); the 2026-05-17 Authentik/Redis CP-taint miss. Pass = correct layer + matching known-issue + no action taken.

## 10e — Stage 2: approval-gated action

- **10e1 — Executor + approval.** ✅ built 2026-10-03 ([`10e-approval-actions.md`](10e-approval-actions.md)). The Toolbelt's executor runs registry templates through Semaphore with a key that is Task Runner on a **dedicated `aiops` project** (roles are per project, so that is what makes the key allow-listed); approval is an operator-only **button press by Discord user id** on a card posted by the Discord bot (webhooks cannot carry buttons or read reactions), bound to the exact params hash; proposals expire after 30 minutes; a kill switch (`/aiops kill`) stops approvals and starts.
- **10e2 — Audit trail.** Structured events (VictoriaLogs) ✅ (every proposal/decision/step is an audit event joined by proposal id; journald -> vlagent) + NetBox journal entries for host-level actions 🔲.
- **10e3 — Verify & rollback hooks.** ✅ built 2026-10-03: the registry `verify` post-condition runs after every action (and `requires_prior` before replay-role); the result is posted to the thread and the card edited; a failed or unverified run says so and shows the registry's rollback note.
- **Exit:** ≥ N real incidents handled via propose → approve → verified, with a complete audit trail.

## 10f — Stage 3: autonomous T1 healing

*As built 2026-10-03: [`10f-autonomous-healing.md`](10f-autonomous-healing.md). One policy (`restart-failed-unit`) is enabled, on the canaries only; the HelmRelease reset and drift-replay policies ship disabled; the syslog-flood vacuum and semaphore auto-apply-on-drift are deferred.*

- **10f1 — Guards first.** Kill switch (Vault KV flag + Discord command), per-target rate limit, circuit breaker, maintenance-window flag, check-mode/diff-scope gate (abort if the dry-run diff is outside the action's declared scope).
- **10f2 — First classes (all T1).** Restart a failed stateless unit; `flux reconcile --reset` for `Stalled`/`RetriesExceeded` HelmReleases; replay drifted baseline on a replica LXC; known-condition cleanups (e.g. the syslog-flood vacuum); Semaphore **auto-apply on detected drift for T1 hosts only**, notify after the fact.
- **10f3 — Soak.** ~14 days on canaries (with injected faults), then real T1 targets. Rollback = flip the kill switch. *Clock started 2026-10-03, read-out about 2026-10-17; scheduled injection (one `zabbix-agent2` stop on a canary every 8 h) switched on 2026-10-05.*
- **Exit:** injected-fault matrix on canaries passes; zero flapping incidents; every autonomous action audited.

## 10g — Stage 4: fleet rebuild loop

*Detailed plan (drafted 2026-10-03): [`10g-rebuild-loop.md`](10g-rebuild-loop.md): ladder of targets, exact sequence, registry shape, the Frigg rebuild-runner proposal for unattended `terraform apply`, hard limits, canary test plan.*

- **10g1 — Prerequisites (pull-forward, not backlog):** ~~offsite export of the Calico datastore + etcd snapshots~~ **done 2026-10-01** — etcd, Vault Raft and Calico objects now land in S3 ([`procedures/offsite-backups.md`](../procedures/offsite-backups.md)); **PBS off Skuld done 2026-10-01**; still open: its datastore capacity (81 % on 2026-10-05); **restore drills passing (done 2026-10-03: etcd, Calico, Vault Raft, PBS canary + replica)** (the offsite-backup restore onto a scratch cluster in 10b2 ✅; PBS restore of a canary and an LXC ✅); Skuld watchdog proven or Skuld de-risked.
- **10g2 — Rebuild loop.** cordon/drain → destroy → Terraform → Ansible → rejoin, proven in order on: canaries → redundant replicas (Mimir/Kvasir, a Tailscale LXC, `do1`) → workers (approval-gated). Quorum members are **leader-aware and never autonomous** (T3).
- **10g3 — Gate for worker auto-rebuild.** Only after N consecutive successful approval-gated worker rebuilds and a passing restore drill.
- **Exit:** a deliberately killed canary and a replica LXC are rebuilt from the repo without operator input; a worker rebuild is approval-gated and verified.

## 10h — Stage 5: predictive & agent-authored change

*Detailed plan (drafted 2026-10-03): [`10h-predictive-change.md`](10h-predictive-change.md): forecasting signals and their data sources, the agent-PR gate, incident drafts. 10h1 is T0 and can start its shadow baseline before 10g.*

- **10h1 — Forecasting.** *(Live: detectors on Frigg since 2026-10-03, notes since 2026-10-04: [`procedures/aiops-forecasting.md`](../procedures/aiops-forecasting.md).)* Disk-fill, memory-headroom, PBS capacity, NVMe latency creep (Urd's DRAM-less Gen 4 drive) → tickets before alerts.
- **10h2 — Agent-authored PRs.** *(Live 2026-10-04: classes `docs`, `drift-note`, `drift`, `k8s`; tested on canaries and burst clusters, not by plan-diff.)* Drift/incident → fix PR → human merge, on the `chart-bump` pattern.
- **10h3 — Incident drafts.** *(Live: the Toolbelt files the write-up request when an incident resolves after crossing the bar and the author opens the PR ([`procedures/aiops-incident-drafts.md`](../procedures/aiops-incident-drafts.md)); the grounded narrative is not built, the draft is mechanical.)* The agent drafts `docs/incidents/` and known-issues updates for human edit.

---

## 10i — Pod rightsizing (VPA recommend-only)

*Detailed plan (drafted 2026-10-04, restarted 2026-10-05): [`10i-rightsizing.md`](10i-rightsizing.md). Goal: the pods on the asgard workers use and reserve less memory over time; the worker VMs keep their Proxmox size. CPU requests (84–90 % requested, [`known-issues/k8s-scheduling.md`](../known-issues/k8s-scheduling.md)) are trimmed by the same loop.*

- **10i0 — Hand-trim** NetBox and `authentik-server` so the recommender fits.
- **10i1 — VPA recommender only**, `updateMode: "Off"` objects in `vpa-config/`, VPA metrics through kube-state-metrics.
- **10i2 — Data and findings (T0):** `kube.rightsizing` joins VPA with 30 days of VictoriaMetrics usage; Gná gets it too.
- **10i3 — Gná's digest:** weekly by default, bi-weekly or monthly by choice; a scoreboard, the top suggestions, tuning advice, and earlier PRs' results.
- **10i4 — Rightsizing PRs:** a resources-only author class from the digest's Draft PR button, burst-tested; the operator merges.
- **10i5 — Post-merge watch** (72 h); a regression offers a revert PR.

---

## Live state (checked 2026-10-05)

*Table read in the morning; rows for the incident sweep, the scheduled injector and the open PRs were refreshed in the evening.*

Read from the running systems (Toolbelt database on Frigg, Semaphore API, kubectl, the hosts), not from the plans.

| Area | Live state |
|---|---|
| Frigg | up since 2026-10-03 00:18 (no reboot since the burst runner was deployed); every `aiops-*` unit active; the burst socket is `0660` group `aiops-burst-clients`; `aiops-draft@2/@4/@12` failed (drafting sessions that made no change) |
| Code deploy | Semaphore `aiops-code-deploy`: the last 52 runs all succeeded; Flux is on `main` `55b5910`, equal to `origin/main`; 6 nodes Ready, no Kustomization/HelmRelease not Ready, no unhealthy pod |
| Other hosts | Gna, Ratatoskr, `do1` and the three canaries are up with no failed units; the PBS LXC is up; no burst droplets exist |
| PBS datastore | 81 % (195 / 240 GB), up from 75 % on 2026-10-03 |
| Toolbelt flags | `autonomy` ON since 2026-10-03 15:57; `kill_switch`, `maintenance`, both breakers off |
| Toolbelt history | 43 incidents, 39 diagnoses, 242 tool calls, 62 chat turns. The incident sweep deployed later the same day closed 18 of the 19 stuck `posted` incidents (13 replay, 2 event, 3 silent); #22 (canary-2) closes at 48 h |
| 10f autonomy log | `restart-failed-unit`: 7 approved, 2 succeeded, 2 failed, 2 verify-failed, 4 skipped; the last matrix action 2026-10-03 18:02 |
| 10f scheduled injector | Live from 2026-10-05 (`aiops-canary-fault` applied). **Injection #1** (canary-1, `zabbix-agent2` stopped 13:31:38) was healed by autonomy in about 5 min 48 s (restart 13:36:26, recorded 13:37:26); the next is about 8 h later. One data point; count the soak from here |
| 10g rebuilds | 6 runs (canary-1, -2, -3), the first five failed in bring-up, the sixth succeeded in 117 s |
| 10h1 forecasts | 7 notes since 2026-10-04 15:29: 3 open (Verd memory slow-fill, ETA 7.1 days, low confidence, labelled useful; Urd `nvme0n1` read and write latency creep, medium), 4 resolved fast-rise (`vor:/`, and the PBS storage once per node); 1 labelled in total |
| 10h2 change requests | 13: 6 merged (PRs #131, #137, #143, #149, #152, #166), 1 open (#167), 2 closed unmerged (the burst-tested k8s PRs #174 and #177), 4 failed (one test error, three no-change sessions) |
| Open PRs | #167 (incident write-up), #186 (drift note), #188 (5h Jellyfin plan), #185 (docker/login-action bump), #79 (1Password mirror of the 10e secrets); #165 (10i plan) merged |

---

## Cost & capacity

| Item | Monthly |
|---|---|
| `do1` (`s-1vcpu-1gb`, reserved IP free while attached; hosts TS3, the proxy **and** the Gatus watcher) | ~$6 |
| **Steady-state DO** | **~$6** (current: ~$12.10) |
| Burst K3s test, 3 × `s-2vcpu-4gb`, 4 h | ~$0.43 per session (~$2–5/mo at light–medium use; always-on would be ~$72) |
| Existing AWS (KMS + state bucket) + offsite backups bucket | ~$1–2 + ~$0.07 (`xiiisins-homelab-backups`, ~3 GB steady state, SSE-S3, no KMS key) |

AWS EC2 for the same always-on footprint would be ~$19–23/mo (public IPv4 now billed; TS3 likely has no arm64 build) and Lightsail only ties DO — so DO stays. Keeping the offsite on a different provider than the Vault KMS key also keeps the outside view independent of the unseal dependency.

---

## Open decisions

| # | Decision | Default |
|---|---|---|
| D1 | Outside watcher placement + tool | **Decided 2026-10-01:** Gatus (systemd binary) co-located on `do1`, direct Discord webhook; split out only on the conditions in 10b3. Not Uptime Kuma |
| D2 | TS3 `do-ts3`/`hel-ts3`/SRV records into Terraform | Yes (overrides "leave hand-managed") |
| D3 | Offsite location for Calico/etcd/Vault Raft exports | **Done 2026-10-01** (built in a separate session): S3 bucket `xiiisins-homelab-backups` (eu-west-1) in the existing AWS account — SSE-S3, versioned, private, TLS-only; separate IAM users (etcd R/W via K3s `etcd-s3-*` on all 3 CPs; PutObject-only writer for the Vault Raft + Calico CronJobs). See [`procedures/offsite-backups.md`](../procedures/offsite-backups.md). Restore drill passed 2026-10-03 (etcd 19 s, Calico 4 s, Vault Raft unsealed in 6 s; drill plan in [`procedures/burst-substrate.md`](../procedures/burst-substrate.md)) |
| D4 | Dedicated Terraform DO token scope | Least-privilege custom scopes; revoke the broad one after 10a. **Amended 2026-10-02:** the custom token lacked `reserved_ip`/`project`/`tag` scopes, so a full-access token was in Vault for a while. **Closed 2026-10-03:** replaced by a custom-scope token (exercised by `terraform plan` + a burst smoke test) and the full-access one revoked |
| D5 | Canary resource-ID range | 1190–1199 (free in the 1101–1199 LXC block) — **canary-1..3 = 1190–1192, live 2026-10-01**. DO nodes' NetBox `VMID` range: 9900–9999 (`do1` = 9900) |
| D6 | Phase numbering | AIOps = **Phase 10** (Phase 9 = Secrets runtime retrieval) |

---

## Recommended sequence

1. **10c1–10c3** (software only) and **10a** (offsite rebuild) in parallel.
2. **10b** (canaries, burst substrate, watcher).
3. **10d** → **10e** sequentially; incident replays gate each.
4. Close the remaining **10g1 prerequisites** (PBS move, restore drills; the offsite export is done) while 10e soaks — they are prerequisites, not backlog.
5. **10f**, then **10g**, then **10h**.

## Definition of done (Phase 10)

- The agent diagnoses the replayed incidents correctly without human help (10d).
- Routine T1 faults heal unattended with an audit trail and a working kill switch (10f).
- A killed replica or canary rebuilds from the repo untouched (10g); worker rebuild is verified and approval-gated.
- The outside watcher independently reports homelab-wide silence.
- Offsite spend stays ≤ ~$12/month at steady state.
