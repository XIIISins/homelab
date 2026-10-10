<!-- docs/plans/active/10g-rebuild-loop.md -->

# Phase 10g — Fleet rebuild loop: plan

*Drafted 2026-10-03. Status: see [`plans/README.md`](../README.md). Parent: [`aiops-roadmap.md`](aiops-roadmap.md) §10g. Mirrors the structure of [`10e-approval-actions.md`](../done/10e-approval-actions.md) and [`10f-autonomous-healing.md`](10f-autonomous-healing.md). Predecessors: 10e (propose → approve → execute → verify) and 10f (the autonomy gate, breaker, kill switch). The rebuild procedures this loop automates are today manual: [`procedures/canary-pool.md`](../../procedures/canary-pool.md) ("Destroy / recreate"), [`procedures/teardown-rebuild.md`](../../procedures/teardown-rebuild.md) (Appendix B, single worker).*

---

## Goal and boundary

Stage 4 of the AIOps loop: when a guest is **dead** or **broken beyond what a restart or a baseline replay can fix**, destroy it and recreate it from the repo (Terraform for the guest, Ansible for the contents, K8s for a worker's node object), then verify it is back, with **no operator input on the canaries and on redundant replicas**, and **one approval click for workers**. This is the fleet-layer version of what Patroni and Flux already do for their layers.

What stays true after 10g:

- **The model still never decides to act.** A diagnosis only supplies a *proposal*. Whether a rebuild runs unattended is the Toolbelt's decision from the registry, which only a reviewed PR can change (same rule as 10f).
- **Rebuild is the last rung, not the first.** Ladder: restart the unit (10f) → start or reboot the guest (new, cheap) → rebuild. A rebuild is only eligible after the lower rungs failed or cannot apply (guest unreachable, start fails, restart breaker tripped, drift not convergeable).
- **A guest is rebuilt only where it was built.** Same node, same VMID, same name, same IP. Never "recreate it on another host". A dead *host* is a host-level fault (principle 2, the 2026-09-30 Skuld freeze): the loop diagnoses and escalates, it never rebuilds guests off a node that is not healthy.
- **State-bearing guests are out.** Quorum members, anything with data that exists nowhere else, the control plane, the agent host and the loop's own brain/mouth are never rebuilt by the loop (see "Hard limits").
- **Off by default, with its own switch.** Rebuild autonomy is a separate master switch from 10f's (`/aiops autonomy on` does not enable it). After every deploy and every Toolbelt database reset it is off.

## Ladder of targets (what the roadmap's "in order" means)

| Stage | Targets | Autonomy | Gate to enter | Notes |
|---|---|---|---|---|
| **A — canaries** | `canary-1..3` (LXCs 1190-1192, Urd) | **auto** (no operator input) | the 10f soak passed, runner + pool + token live | no data, no VIP, no consumers; the test bed for everything below |
| **B1 — AdGuard replicas** | `mimir` (1111, Verd), `kvasir` (1112, Skuld) | approval for the first 2 clean rebuilds each, then **auto** | stage A matrix passed; PBS restore of an LXC proven; `neighbours_healthy` guard | Saga (1110) is the Terraform/sync origin and VRRP priority holder: **not** in scope |
| **B2 — a Tailscale LXC** | one of the subnet-router pair (`bifrost` / `heimdall`) | **approval-gated** (cannot be unattended, see below) | the root-ticket decision (open question) | `gjallarbru` is the sole exit node (singleton): out |
| **B3 — `do1`** | the offsite droplet | **approval-gated** first, autonomy only once its two prerequisites exist | fast homelab-side `do1` check; TS3 DB restore automation | it is also the outside watcher: see "do1" below |
| **C — workers** | `einherjar-urd/verd/skuld` (VMs 2101-2103) | **approval-gated** (T2) | the 10g3 gate below | local-path data on the node is lost by design: see the data manifest |

`hermod` (1103, T1 in the registry) is a candidate for a later B-stage row; it is the alert path, so it starts approval-gated. It is not needed for the exit criteria.

## Sequence (the exact order of operations)

Every step is idempotent and **resumable from state**: re-proposing the same rebuild after a mid-way failure picks up from what Terraform and the cluster say, it does not replay blindly.

### LXC and droplet classes (canary, replica, offsite)

1. **Eligibility** (the Toolbelt, reading reality itself, never the model's say-so): the guest is *dead* (PVE says stopped or hung and a start fails, or `reach.tcp` fails 3 probes over >= 10 min and the Zabbix agent is silent) or *broken* (the 10f restart breaker tripped for this target in the last 24 h, or a dry-run replay shows drift larger than `max_changed` that a role replay cannot fix). **And the host node is healthy** (PVE node status online, its other guests answering). A node-level fault ends here as diagnosis-only.
2. **Class guards:** target is on the rebuild allow-list for its class; peers healthy (`neighbours_healthy`); target is not the current leader/VRRP master unless it is already dead; rate limits; breaker closed; maintenance flag off; no other rebuild in flight (queue length 1).
3. **Last-chance backup** (classes with any data; skipped for canaries): if the guest is alive but broken, take an on-demand PBS backup first (also preserves the broken state); if dead, require a successful nightly PBS backup within 36 h. This is the undo path, which is why the restore drill gates stages B and C.
4. **Plan** (`rebuild-plan`, T0): on Frigg's rebuild runner, `terraform plan -replace=<address> -target=<address> -out=<file>` from the runner's clean `main` checkout. The plan is machine-checked: exactly **one** resource change, either `replace` of an existing guest or `create` of a missing one (a guest deleted behind Terraform's back); identity attributes (name, VMID, node, IP, VLAN, template) unchanged; nothing else in the plan. Output: plan hash + the `origin/main` commit it was made from. A plan that fails any check ends the proposal (`cancelled`), nothing was changed.
5. **Destroy + create** (`rebuild-apply`): `terraform apply <planfile>` (the saved plan, so what runs is what was checked), refusing if the plan is older than 15 min or `origin/main` moved since. The LXC/droplet is created empty; NetBox needs no change (identity is identical, see the standing TF-to-NetBox rule).
6. **Converge** (`rebuild-converge`, through Semaphore, the existing executor path): Day-1 baseline as `root` (the new guest has only the Terraform-injected key), then the class's full play as `ansible` (hardening locks root out at the end). The wrapper waits for SSH first and only runs the Day-1 part if the guest still answers as `root`. `host_key_checking = False` in `ansible.cfg`, so a changed host key needs no `ssh-keygen -R` from the loop (operators still need it for their own shells).
7. **Verify** (`rebuild-verify`, T0): class-specific post-conditions (table below) plus: the originating alert has cleared, the Zabbix host reports, the agent ships logs to VictoriaLogs, NetBox still matches. A guest that is up but fails verify ends `verify_failed` and trips the rebuild breaker.
8. **Announce** in the incident thread: what was destroyed, plan hash, durations per step, verify results, and the registry's rollback note (restore from PBS).

### Workers (VMs, K3s nodes, approval-gated)

Pre-flight, shown on the approval card (the card is the data-loss manifest, so the operator approves knowing what dies):

- all other nodes Ready, etcd 3/3, no other drain or upgrade in flight, `maintenance` off;
- **local-path PV manifest** for the node (`kubectl get pv` with node affinity): each PV tagged *replicated* (Vault Raft member) or *single-instance* (the only copy dies with the node). Single-instance PVs with no PBS backup of the worker `/data` disk in the last 24 h **block** the proposal;
- iSCSI PVCs attached to the node are listed (they detach cleanly; a stale session elsewhere is the known failure, see [`storage-iscsi-synology.md`](../../known-issues/storage-iscsi-synology.md));
- Vault: unsealed, 3 peers; if the target hosts the Raft leader, **step down first** (needs a narrow identity, see "What is missing").

Then: cordon + drain (timeout-bounded; Vault's required anti-affinity leaves the displaced pod Pending by design, 2/3 voters for ~25 min is accepted, see decisions "Stateful worker rebuild") → backup gate re-checked → `rebuild-plan` / `rebuild-apply` on the single VM address (`reboot_after_update` is already false on workers) → `kubectl delete node <name>` (drops the stale node object and the K3s node-password secret that would otherwise reject the new agent) → `rebuild-converge` = Day-1 baseline as root, then `asgard-k3s.yml --limit <name>` (the `k3s` role joins it; `local-path-disk` formats the fresh 50 GB `/data`) → uncordon → verify: node Ready, Calico pod Running on it, Vault 3/3 voters after the Pending pod reschedules, no PVC Pending, all HelmReleases Ready, local-path-provisioner serving, the originating alert cleared. Timing reference: the manual run on 2026-05-22 took ~25 min.

**Control-plane VMs are never in this loop** (T3, etcd members); their manual procedure (teardown-rebuild Appendix A) is unchanged.

### Class post-conditions (what `rebuild-verify` checks)

| Class | Checks beyond "SSH as `ansible` works, agents up, alert cleared" |
|---|---|
| canary | `vlagent` + `zabbix-agent2` active; Zabbix host in `Asgard/LXCs/Canary`; the "Canary smoke test" template linked |
| AdGuard replica | `AdGuardHome.service` answers a query on its own IP for a Terraform-managed rewrite (`smoketest.niflheim.xiiisins.com`); keepalived running with the right priority; the adguardhome-sync last run succeeded and the replica's rewrites match Saga; the VIP `10.0.10.200` answers throughout |
| Tailscale LXC | `tailscaled` up, tagged, advertising its routes, approved; the peer router still online |
| `do1` | public `/health` via the reserved IP; `teamspeak` + `plantnet-proxy` containers up; Gatus up and heartbeat from Frigg accepted; tailnet node tagged `tag:offsite` |
| worker | the worker sequence above |

## What exists, what is missing

**Exists (reused unchanged):** Terraform modules with pinned identity for every class (`asgard-lxcs` for canary/AdGuard/Hermod, `asgard-lxcs-root` for the Tailscale trio, `asgard-k3s` for workers with `reboot_after_update = false`, `terraform/digitalocean` for `do1`); the documented lossless `-replace` of a canary; the Day-1-then-full Ansible flow, `asgard-canary.yml` / `site-nonprod.yml`; NetBox inventory (identity unchanged, so no NetBox write); the `k3s` role with `--limit` join and the 2026-05-22 validated worker procedure; Frigg with `terraform` 1.15.5, the Vault-backed shim and the burst substrate for drills; the 10e engine (registry validation, params-hash approval, executor, verify, kill switch, expiry), the 10f gate (`consider_auto`, precheck, breaker, rate limits, `auto_log`, report), the Semaphore `aiops` project, the replay harness and `scripts/canary/fault`.

**Missing (to build; the slices below):**

| Piece | Why it is needed |
|---|---|
| **Rebuild runner** on Frigg (own unix user, unix socket, peer-credential check) | the only place Terraform may run unattended; see "Running `terraform apply` from an automated path" |
| PVE **resource pool** `aiops-canary` (and later `aiops-replica`) with the guests' Terraform `pool_id`, plus a **scoped PVE API token** with an ACL on the pool only | makes "the runner can only touch canaries" a Proxmox-enforced fact, not a code promise |
| Registry `rebuild:` section (classes, targets, guards, limits, policies) + schema + lint, and `steps` / `backend` support in `actions.py` (today one template plus `requires_prior`) | a rebuild is plan -> apply -> converge -> verify, with the apply bound to the plan hash |
| Actions `rebuild-plan`, `rebuild-guest`, `rebuild-worker`, `rebuild-verify`, `start-guest` (rung 0) + playbooks (`aiops-rebuild-converge.yml`, `aiops-rebuild-verify.yml`) + Semaphore templates (operator apply) | the registry shape below |
| New precheck types `guest-dead`, `guest-broken`; runbooks `RB-GUEST-DEAD`, `RB-GUEST-BROKEN`; alert routing for them | the Toolbelt must read reality itself, and the diagnosis must name the runbook |
| A High Zabbix trigger for "canary unreachable" (the stock one is Average and Gná only sees High/Disaster) | the canary kill test has to reach the agent |
| Separate credentials: Vault AppRole + policy for the runner; narrow state IAM identity; (workers) a drain ServiceAccount and a `sys/step-down`-only Vault policy; (`do1`) a scoped DO token copy | least privilege per stage; none of these may be the operator's admin credentials |
| `scripts/canary/fault kill` (PVE API stop, VMID-validated 1190-1192) and replay scenarios (`canary-dead`, `rebuild-node-down`, `rebuild-refused`) | the acceptance harness |
| Bot: the card shows plan summary, data-loss manifest, last-backup age | approval must be informed |
| `do1`: a fast homelab-side check (the 10b3 follow-up) and TS3 DB restore automation | only way a dead `do1` is noticed in minutes; TS3 DB is restored by hand today |
| Procedure doc `procedures/aiops-rebuild.md` (deploy, switches, acceptance) | written with the build, like `aiops-autonomy.md` |

## As built so far (10g1 pure logic, 2026-10-03)

Code only, no live system touched; `python3 aiops/tools/lint.py` and the `aiops/tests` suite pass.

| Piece | Where | State |
|---|---|---|
| `eligible(facts, policy)`: verdict `go` / `skip` / `stop` plus a reason code, covering dead vs broken, the ladder (start before rebuild), node health first, deny list and class allow-list, never quorum/state-bearing/agent host, leader-aware, peers healthy, rate limits and the 1-failure breaker, maintenance and kill switch, queue length 1, backup freshness, and who may press the button (stage autonomy, `autonomy_rebuild`, an enabled policy, two approvals before auto for B1) | `aiops/toolbelt/rebuild.py` | built, table-tested; not called by anything yet |
| `check_plan(plan_json, expected)`: exactly one `replace` or `create` of the expected address, identity (name, vmid, node, ip, vlan, template) unchanged, nothing else changed or destroyed, no drift elsewhere. Reads the bpg/proxmox attribute paths used in our modules (documented in the module) | same | built, tested against hand-written plan fixtures; a recorded real `terraform show -json` fixture is still to add when the runner exists |
| `worker_data_manifest(pv_json, pods_json, backup_age_hours, node)`: local-path PVs on the node tagged replicated (Vault Raft member) or single-instance, iSCSI PVCs listed, `blocks` when single-instance data has no PBS backup within 24 h, a sanitised bounded card text | same | built, tested |
| Registry `rebuild:` section (classes and hosts with vmid and node, stages A to C, limits, deny list, policies, all disabled, `autonomy_rebuild_default: false`) + `host_tiers.T2` + five actions (`rebuild-plan`, `start-guest`, `rebuild-guest`, `rebuild-worker`, `rebuild-verify`) as `applied: false` / `planned: true` | `aiops/actions.yml`, `aiops/schema/actions.v1.schema.json` | built |
| `check_rebuild` lint: hosts exist in `host_tiers`, no control-plane node, quorum member or PBS in any list (and the deny list cannot shrink), canaries only in stage A on Urd, only stage A may start unattended, enabled policies need an `auto` ceiling and a runbook, action tiers pinned | `aiops/tools/lint.py` | built |

Side effects worth knowing: planned actions are hidden from the chat agent's `propose_action` description (`aiops/n8n/build_ingest.py`), so the model is never told about an action that cannot run; the engine itself is untouched, so today the only thing stopping a proposal of these actions is the `applied: false` gate (the engine has no rebuild-class target guard until slice 2). `RB-GUEST-DEAD` / `RB-GUEST-BROKEN` do not exist yet, which is why no policy can be enabled (lint requires the runbook for an enabled one).

**Not built (after 10g1):** runner, PVE pool and scoped token, engine `steps` / `backend` and the `guest-dead` / `guest-broken` prechecks, `autonomy_rebuild` and the rebuild breaker in the engine, converge/verify/plan playbooks and Semaphore templates, the canary High trigger and `RB-GUEST-*` runbooks and routing, `scripts/canary/fault kill|destroy`, replay scenarios, bot cards, PBS last-chance backup, Vault/Kubernetes drain identities, `do1` fast check, the procedure doc. The engine, prechecks, flag, breaker and bot items are now built: see 10g2 below.

### 10g2: the engine support (2026-10-03, code only, everything faked in tests)

`aiops/toolbelt/rebuild_exec.py` (new; shipped by the `aiops-toolbelt` role) is called from `actions.Engine` for any action whose registry entry declares `steps:`, so `actions.py` only grew hooks. Nothing is deployed and the five actions are still `applied: false` / `planned: true`: the tests open the gates in a copy of the registry.

| Piece | What exists | What is faked in tests |
|---|---|---|
| `steps` / `backend` | registry `steps:` per action (`scope: rebuild` guards the target); `backend: runner` (steps `plan`, `apply`) or `semaphore` (default; the action's own template, or another T0 `action`, with extra `vars`); the step named `verify` runs the class checklist; schema and lint cover the shape | scripted Semaphore, `FakeRunner` |
| `RunnerClient` | JSON line over a unix socket (`/run/aiops-rebuild/runner.sock`), injectable transport, `request_id` echo check, protocol error codes as `RunnerError`; a real socket round-trip is tested | `FakeRunner` is a transport function |
| Flow | on **propose**: eligibility from reality (`rebuild.eligible`), then a plan from the runner (or a recorded `rebuild-plan` result named by `plan_hash`) validated against the registry again, stored with its id, `origin/main` and expiry; the proposal expires with its plan and its `plan_hash` param IS the plan id. On **execute**: eligibility is re-read (a guest that healed is `skipped`), then `apply` (refuses an expired plan, a plan hash that differs from the bound one; the runner's `origin-moved` etc. are surfaced), `converge` through the Semaphore path (`applied` gate honoured for every action the flow runs), `verify` | runner, Semaphore |
| Facts | `FactsProvider` (PVE guest and node status, `reach.tcp` probes accumulated in `rebuild_probes`, Zabbix agent silence) with `ReaderFacts` over the existing read tools; conservative (unreadable = unknown = do not act; an unknown VRRP master counts as the master; no PBS read tool yet so a class needing a backup stops on `backup-stale`; a worker manifest has no source yet so worker rebuilds stay blocked) | `FakeFacts` |
| Verify | `VerifyProvider` (`SemaphoreVerify` runs `rebuild-verify`, expecting `checks: {condition: bool}` in its `AIOPS_RESULT`) and the pure `run_checklist` (every class post-condition present and true; nothing checked is a failure) | `FakeVerify` |
| Autonomy | precheck `guest-dead` / `guest-broken` in `consider_auto` for rebuild-section policies (policy chosen by the diagnosis's runbook; the Toolbelt's own dead/broken reading must agree), `guest-dead` for `start-guest`; separate flag `autonomy_rebuild` (default off; operator-only, the system can never turn it on; `/aiops autonomy` is untouched); breaker flag `autonomy_rebuild_breaker` (tripped by the system on the first failure **after an apply was attempted**, cleared only by an operator; a refusal before the apply costs nothing); limits from `rebuild.limits`; queue length 1 (fleet-wide, also enforced at the door of the executor); maintenance and kill switch respected (the kill switch blocks starting; a started apply finishes its converge) | |
| Recovery | a rebuild whose apply had begun stays `running` across a restart and is resumed from state: it re-attaches to the Semaphore task it recorded, re-runs a read-only verify, and for an interrupted apply asks the runner (`status`); it never re-sends an apply, and an unconfirmed apply fails with the breaker tripped for a human. A restart before the apply just ends the proposal | |
| Visibility | state `rebuilding` (the run's step) in `/aiops status`, `/aiops report` and the card; `/aiops rebuild on\|off\|reset-breaker`; cards carry the plan, "what gets destroyed", the data-loss manifest and the last-backup age; the announcement carries per-step timings; a rebuild breaker notice | |

Runner protocol (v1, implemented by the client; the runner is slice B). One JSON object per line over a unix stream socket at `/run/aiops-rebuild/runner.sock`.

| Request | Response |
|---|---|
| `{"v":1,"request_id":"<uuid>","op":"plan","class":"canary","target":"canary-2"}` | `{"v":1,"request_id":"..","ok":true,"plan_id":"<sha256>","address":"<tf address>","summary":{"action":"replace","changes":1,"identity":{"name","vmid","node","ip"}},"origin_main":"<sha>","expires_at":<unix>,"problems":[]}` (`ok:false` with `problems` when the plan check rejects) |
| `{"v":1,"request_id":"..","op":"apply","plan_id":"<sha256>"}` | `{"ok":true,"result":{"applied":true,"resources":1},"seconds":<n>,"origin_main":"<sha>"}` or `{"ok":false,"error":"<code>: <text>"}` with codes `plan-unknown`, `plan-expired`, `origin-moved`, `denied`, `terraform-failed`, `busy` |
| `{"v":1,"op":"status"}` | `{"ok":true,"busy":false,"last":{...}}` |

The runner never receives free-form commands: only class, target and plan id. The first five error codes mean nothing was changed (no breaker); `terraform-failed`, an unreachable runner or a garbled answer during the apply do trip the breaker. Two assumptions the client makes that the runner must meet: `status.last` carries `plan_id` and `ok` of the last apply (otherwise a restart mid-apply escalates to a human, which is safe but not automatic), and `summary.identity` carries `name`, `vmid` and `node` (checked against the registry).

Assumptions about slice C's playbooks: `aiops-rebuild-converge` reads `class`, `converge` (the class's play name), `target` and the task `limit`, and ends with `AIOPS_RESULT {ok}`; `aiops-rebuild-verify` returns `checks` (one boolean per class post-condition); `aiops-rebuild-worker` takes a `phase` var (`drain` before the apply; `converge`, which deletes the node object, joins and uncordons, after it). Still not built in 10g2: the runner, drain/uncordon logic, PBS last-chance backup (a read tool and an on-demand job), the worker manifest source (`kubectl get pv` is not on the read-tool allow-list), a VRRP-master read, wiring `runner_socket` and the probe cadence in `core.py` / `server.py` (the Engine builds a `RunnerClient` and `ReaderFacts` itself when given a socket path and a reader), and flipping any `applied` or policy flag.

**Slice B (runner and its infrastructure, code only, not deployed):** the rebuild runner (`aiops/runner/rebuild_runner.py`, fake-terraform tests), the PVE pool + pool-scoped token plan code (`terraform/proxmox/asgard-pools/`, canaries get `pool_id`), the runner's Vault policy + AppRole (`terraform/vault/rebuild-runner.tf`) and the Ansible role + playbook (`aiops-rebuild-runner`, `asgard-rebuild-runner.yml`). Deploy order, threat model and acceptance: [`procedures/aiops-rebuild.md`](../../procedures/aiops-rebuild.md).

## As built: slice C (playbooks, detection, harness; code only, nothing applied)

| Piece | Where | State |
|---|---|---|
| `aiops-start-guest.yml` (rung 0: `pct start` through Urd, hostname-checked, canary VMID 1190-1192 only), `aiops-rebuild-converge.yml` (guard needs `--limit` equal to `target`; waits for SSH; Day-1 baseline as root only where root still answers; then `asgard-canary.yml` as `ansible`), `aiops-rebuild-verify.yml` (the six canary post-conditions as separate fields, Zabbix read through the JSON-RPC API; fails the run when any is false) | `ansible/playbooks/` | built; ansible-lint and `--syntax-check` clean; never run |
| Semaphore templates `aiops-start-guest`, `aiops-rebuild-converge`, `aiops-rebuild-verify` in the `aiops` project | `terraform/semaphore/templates.tf` | HCL only; `terraform fmt` clean; **operator apply pending**; the registry entries stay `planned: true` until then |
| `RB-GUEST-DEAD`, `RB-GUEST-BROKEN` (`automatable: approval`: `rebuild-guest` caps at approval until a policy is enabled), route `zbx-canary-guest-down` before `zbx-host-unavailable`, native fixture, replay scenarios `canary-dead` (a burst of two correlated alerts) and `rebuild-refused` (Saga, deny-listed) | `aiops/` | built, lint passes; replays not run live |
| `Canary smoke: guest unreachable (ICMP ping loss)` (High, `icmpping[,3]`) | `canary-smoke-test.yml` | built; import and `fping` on Hugin to confirm at the operator apply |
| `scripts/canary/fault kill|destroy` + `aiops/tests/test_rebuild_playbooks.py` (argument validation through the `FAULT_VALIDATE_ONLY` hook, playbook safety text, template names, trigger routing) | `scripts/canary/` | built, tested |

Findings: `rebuild-plan` and `rebuild-worker` playbooks are not in this slice (plan runs on the Frigg runner; workers are stage C). The playbooks assume the engine passes `target` (and `plan_hash` to converge) and sets the task `limit` for converge; verify and start-guest must run without `--limit` or, for verify, with one equal to the target. The registry's `rebuild-guest` still lists `semaphore.template: aiops-rebuild-converge` as a single template; the plan -> apply -> converge -> verify composition is the engine slice.

## Registry shape

A sketch of the shape (names, tiers and ceilings are the decision; field spelling is finalised in the PR that adds the schema). It extends 10f's pattern: a **scope** section a reviewed PR can widen, and a **policy** per class that points at an action.

```yaml
rebuild:
  limits:
    per_target_per_day: 1        # a second rebuild inside 24 h means the rebuild did not fix it: escalate
    per_target_per_week: 2
    per_class_per_day: 2
    fleet_per_day: 3
    breaker_failures: 1          # a failed or unverified rebuild leaves a half-built guest: stop and page
    plan_max_age_seconds: 900
  classes:
    canary:   {targets: [canary-1, canary-2, canary-3], module: asgard-lxcs, pool: aiops-canary, backup: none, converge: asgard-canary}
    adguard-replica: {targets: [mimir, kvasir], module: asgard-lxcs, pool: aiops-replica, backup: pbs-last-chance,
                      neighbours: [saga, mimir, kvasir], leader_aware: vrrp-master, converge: asgard-adguard}
    tailscale-router: {targets: [bifrost, heimdall], module: asgard-lxcs-root, backup: pbs-last-chance, converge: asgard-tailscale}   # approval only
    offsite:  {targets: [do1], module: digitalocean, converge: do1}                                                                # approval first
    worker:   {targets: [einherjar-urd, einherjar-verd, einherjar-skuld], module: asgard-k3s, kind: k8s-worker}                    # approval only
  deny:                                  # refused at validation AND re-checked in the runner
    names: [saga, fulla, vor, idunn, hlin, eir, snotra, hugin, factorio, gna, ratatoskr, frigg, gondul, hlokk, sigrun, pbs]
    vmids: [1101, 1102, 1110, 1120, 1121, 1122, 1130, 1131, 1132, 1133, 1134, 1135, 2001, 2002, 2003, 2900]
  policies:
    rebuild-dead-canary:  {enabled: true,  action: rebuild-guest, class: canary, runbook: RB-GUEST-DEAD, layers: [host, workload],
                           min_confidence: medium, precheck: guest-dead}
    rebuild-broken-canary: {enabled: true, action: rebuild-guest, class: canary, runbook: RB-GUEST-BROKEN, precheck: guest-broken}
    rebuild-dead-replica: {enabled: false, action: rebuild-guest, class: adguard-replica, runbook: RB-GUEST-DEAD, precheck: guest-dead}  # flipped by PR after 2 clean approvals each
```

| Action | Tier | `max_autonomy` | Guard (allow-lists) | Verify | Rollback note |
|---|---|---|---|---|---|
| `start-guest` | T1 | `auto` via policy, classes canary/replica | target in a `rebuild` class; node healthy; start only (never create/destroy) | `guest-status` running + `reach.tcp` | none needed |
| `rebuild-plan` | T0 | `auto` | target in class allow-list, not on `deny`, plan shape rules above | plan is exactly one in-scope change; hash recorded | nothing changed |
| `rebuild-guest` (steps: plan, apply, converge, verify) | T1 | `approval`; `auto` only through a `rebuild.policies` entry, per class | class guards + `requires_prior: rebuild-plan` with the same hash; one in flight | `rebuild-verify` per class | restore the last-chance PBS backup to the same VMID (`pct restore`); for canaries nothing to restore, just rebuild again |
| `rebuild-worker` | T2 | `approval` (always, until 10g3) | worker allow-list; pre-flight manifest clean; Vault 3 peers; backup recent | worker post-conditions | the node is replaceable; data restore is the worker `/data` PBS backup (single-instance PVs) or Raft resync (Vault) |
| `rebuild-verify` | T0 | `auto` | any rebuild-class target | per-class checks | read-only |

**How it plugs into the engine.** The 10e/10f engine runs one Semaphore template per action. The additions are small and local: (1) `backend: runner | semaphore` per step, where the runner implements the same four-method client protocol the engine already uses (`template_id/start/status/output`) and emits the same `AIOPS_RESULT {...}` lines, so `parse_output`/`evaluate` are reused; (2) `steps:` for a composed action with fail-stop, one audit event per step, and the plan hash passed from the plan step to the apply step (params-hash binding extended to "what was planned"); (3) `consider_auto` gains the class lookups, the `guest-dead`/`guest-broken` prechecks and a separate master flag `autonomy_rebuild` and breaker `rebuild_breaker` (re-armed by `/aiops autonomy reset-breaker rebuild`). `static_block`, rate limits, kill switch, maintenance, `auto_log` and `/aiops report` apply unchanged. The kill switch blocks **starting** a rebuild; once `rebuild-apply` has begun the run finishes its converge (a bare, half-built guest is worse than a finished one), and the thread says so.

## Hard limits (enforced in registry, runner and playbooks, each independently)

- **Never quorum members, autonomously or otherwise, in this loop:** Vault pods, K3s CPs/etcd, Patroni nodes (Fulla/Vor/Idunn), the HAProxy/etcd trio, plus PBS (T3, and **never back on Skuld**; the loop has no code path that creates PBS), PVE hosts, Synology, UCG, Frigg, and the loop's own Gna and Ratatoskr (a rebuild of the brain or the mouth is an operator action until a second control path exists).
- **Leader-aware.** For classes with a leader notion the target must not hold it unless already dead (AdGuard: not the VRRP master). The invariant "at most one of Saga/Mimir/Kvasir is not serving" is checked before and during. The loop never rebuilds two members of the same redundancy set within 24 h of each other.
- **State-bearing excluded:** anything whose data is not reproducible from the repo plus Vault: Postgres, Factorio, Hugin/Zabbix, NetBox/Authentik (K8s apps), Garage, Immich. A worker is allowed only because its data is either replicated or backed up, and only with approval and the manifest.
- **Node health first.** Never rebuild on a node that is not healthy (the Skuld lesson). Never "heal" a freeze by rebuilding its guests.
- **Per-day caps and breaker:** the `rebuild.limits` above. Breaker at 1 failure: unlike a unit restart, a failed rebuild leaves something half-built and needs a human. Only the system trips it, only an operator re-arms it.
- **One rebuild at a time, fleet-wide;** maintenance flag (set by `proxmox-host-patching.yml`, `k3s-upgrade.yml`, `calico-upgrade.yml`) suppresses autonomy.
- **Runner refuses what the Toolbelt should have refused** (defence in depth): it re-reads `aiops/actions.yml` from its own checkout and applies the deny list, the plan-shape rules and the address allow-list itself.

## Running `terraform apply` from an automated path

The repo rule is "`terraform apply` only from the main checkout, never a worktree"; its purpose is intentionality: one serialised, deliberate apply, not parallel agents racing on state. An unattended loop cannot be the operator's laptop, so the question is how to keep the purpose.

| Option | What it is | Verdict |
|---|---|---|
| **1. Operator-only apply** | the loop proposes, the operator runs `terraform apply -replace=...` by hand | keeps the rule literally but is not "no operator input"; remains the path for everything T3 and for the Tailscale/root-ticket class |
| **2. Frigg rebuild runner** (recommended) | a systemd service on Frigg under its own unix user, listening on a unix socket (peer-credential check: only the Toolbelt's uid). It owns a **dedicated clean checkout** of `main` (`git fetch` + verify `HEAD == origin/main` and a clean tree, refuse otherwise; no one edits it), a single-flight lock, and its own Vault AppRole for the scoped PVE token and the state credentials. It runs only `plan -replace -target` then `apply <saved plan>` for one allow-listed address, after re-validating against the registry | **recommended** |
| 3. A Semaphore template runs Terraform | reuses the executor path | rejected: the Semaphore pod has no `terraform`, no PVE or AWS credentials, and putting a PVE write token inside the K8s cluster would make a **worker rebuild depend on the thing being rebuilt** |
| 4. GitHub Actions apply on merge | CI-driven apply | rejected: hosted runners cannot reach PVE; a self-hosted runner on a public repo executes PR-controlled code next to the credentials |
| 5. Direct PVE API (`pct destroy`/`create`) | skip Terraform | rejected: state drift and a second source of truth, violating the IaC invariant |

Why option 2 keeps the rule's intent: the checkout is the reviewed `main` (nothing reaches it except through the PR + CI gate), there is exactly one, never a worktree, applies are serialised by lock and by the engine's queue length 1, the apply is a **saved plan that a machine checked to be exactly one in-scope replace**, and the Terraform backend lock (`use_lockfile`) still protects state. What it changes is *who* may press the button, so it is **an operator decision**: confirm the exception (and, when accepted, amend the CLAUDE.md invariant line: "terraform apply from the main checkout, or from the Frigg rebuild runner's dedicated `main` checkout for allow-listed `-replace` of rebuild-class guests") and add a decisions row. Until it is accepted, stage A can run in **approval mode** (the click authorises the runner), which exercises everything except the unattended part.

PVE-side least privilege: the token's role is the minimum Terraform needs to create/destroy a container in the pool (the exact privilege set is found empirically with a negative test, like the read-only proofs in `aiops/tools/verify_readonly.py`: it must fail to touch Urd's other guests, PBS 1101 included). A guest outside the pool cannot be replaced by this token even if every software check were bypassed.

## Prerequisites still open (pull-forward, not backlog)

| Prerequisite | State on 2026-10-03 | Gates |
|---|---|---|
| 10f soak passed (14 days, injected-fault matrix, zero flapping) | code complete, deploy + soak pending | stage A (autonomy) |
| Offsite-backup **restore drill** on a burst K3s ([`burst-substrate.md`](../../procedures/burst-substrate.md)) | substrate live and smoke-tested 2026-10-02; **drill not run** | stage C and the 10g3 gate |
| **PBS restore of an LXC and of a canary**, scheduled via Semaphore ([`open-questions.md`](../../operations/open-questions.md)) | built and proven by hand 2026-10-10 (`pbs-restore-test`, weekly Semaphore schedule; [procedure](../../procedures/pbs-restore-test.md)); first scheduled run pending | stage B (the undo path) |
| ~~PBS datastore capacity~~ | dropped as a prerequisite 2026-10-10 (67 %; the large consumers aged out) | the forecast note and the Zabbix disk triggers still cover it |
| Skuld watchdog proven or Skuld de-risked | `iTCO_wdt` canary only | not a gate for canaries (Urd); the node-health guard handles Skuld guests (`kvasir`, `einherjar-skuld`) |
| Worker data inventory (which local-path PVs are single-instance) | not written down | stage C pre-flight |
| `do1` fast homelab-side check; TS3 DB restore automation | follow-ups open | stage B3 autonomy |
| The terraform-apply policy decision | open | unattended anything |

## Decisions

| Question | Choice | Why |
|---|---|---|
| Where Terraform runs | the Frigg rebuild runner on a dedicated clean `main` checkout, saved-plan apply | the only unattended-capable option that does not put write credentials in the failure domain or run PR code next to them |
| What bounds the runner | a PVE pool ACL on the token, plus registry allow-list, plus deny list, plus plan-shape check, each enforced separately | any one layer failing must not be enough |
| Rebuild vs restart | rebuild only after restart/start fail or cannot apply | rebuild is the only destructive rung; the cheap rungs fix most faults |
| Where a guest is rebuilt | same node, same identity, only when the node is healthy | host faults are diagnosed, not "healed" by moving guests (principle 2) |
| Master switch | `autonomy_rebuild`, separate from `autonomy` | enabling restarts must not silently enable destroys |
| Breaker threshold | 1 failure, and a second rebuild in 24 h escalates | a repeat means the rebuild is not the fix |
| Kill switch semantics | blocks starting; a started apply finishes its converge | half-built is worse than finished |
| Tailscale LXCs | approval-gated; operator-run apply (root ticket) | `device_passthrough` needs root@pam ticket auth, which an unattended runner must never hold; see open questions for the host-side alternative |
| Worker auto-rebuild | not in 10g; the 10g3 gate defines the road | local-path data and Vault anti-affinity make it T2 until proven |

## Test plan (canaries first, then replicas)

All fault injection stays inside the rules in [`canary-pool.md`](../../procedures/canary-pool.md) ("Fault injection: stay scoped to `canary-*`"): only VMIDs 1190-1192, never a group or glob, never anything host-global.

1. **Plan only (T0).** Propose `rebuild-plan canary-2`: the card shows exactly one `replace`, identity unchanged. Then the negative set, each must be refused with its own reason: `saga`, `vor`, `gondul`, `pbs`/VMID 1101, a name not in any class. A plan with two changes, or a changed identity attribute, is proven in unit tests against recorded plan fixtures (the live runner only ever plans from `main`, so it cannot be fed a bad plan on purpose).
2. **Approval-gated rebuild.** Approve a `rebuild-guest canary-2`; record per-step durations (estimate to confirm: LXC create ~1-2 min, converge ~4-6 min, total under ~10 min); verify passes; Zabbix and VictoriaLogs show the new guest; NetBox unchanged.
3. **The real kill test.** `scripts/canary/fault kill canary-2` (new; PVE API stop, VMID-validated). Expect: the High "canary unreachable" problem, Gna diagnosis naming `RB-GUEST-DEAD`, `start-guest` heals it (rung 0, **no rebuild**). Then make start impossible (`fault destroy canary-2`, deleting the container behind Terraform's back: the plan is a `create`, which the shape rules accept) and, with `autonomy_rebuild` on, watch the unattended path: precheck, plan, apply, converge, verify, announcement. Pass: guest verified with no operator input; **time from fault to verified under 15 min** (target to confirm on first run).
4. **Broken, not dead.** Fill the canary rootfs or break its baseline so `restart-failed-unit` fails 3 times and the 10f breaker trips; expect `RB-GUEST-BROKEN`, a rebuild, verify. The 10f breaker and the rebuild loop must not fight (the rebuild resets the target's restart budget).
5. **Safety matrix.** (a) two canaries killed at once: strictly one at a time, the second waits or expires, never parallel; (b) rebuild of the same canary twice in 24 h: the second refused and escalated; (c) kill switch engaged before start: nothing starts; engaged mid-apply: the run finishes converge and says so; (d) a rebuild that fails (revoke the pool ACL for `canary-3`, expect a plan/apply error): breaker trips at once, thread shows the failure and the resume path, **no retry loop**; (e) replay scenario `rebuild-node-down` (the `skuld-freeze` shape: 5 alerts, one hypervisor) must produce diagnosis only and zero rebuild proposals for guests on that node; (f) a prompt-injected diagnosis naming a denied target or an out-of-class class is refused at validation.
6. **Replica stage (B1).** `pct stop 1111` (Mimir) in a quiet window with a client running `dig @10.0.10.200` once a second throughout. Approval-gated twice per replica, then unattended. Pass: verify (per-class table), the VIP never stops answering except the VRRP failover blip if Mimir was master (budget: at most 2 consecutive failed queries; the guard normally prevents rebuilding a live master), sync parity restored. Also prove the PBS last-chance backup and a restore to a throwaway CT first.
7. **Persistence.** The runner and its units are reboot-tested on Frigg before declaring done (CLAUDE.md "Persistence validation"); a Frigg reboot mid-idle leaves no stale lock and no half-applied state (a mid-run restart ends `failed` and the next proposal resumes).
8. **Workers (C), approval-gated, one at a time, a different worker each,** on the real cluster in a quiet window and only after the drill prerequisites: record the manifest the card showed vs what actually died, Vault voters before/after, time to Ready.

## Exit criteria

From the roadmap: a deliberately killed **canary** and a **replica LXC** are rebuilt from the repo without operator input; a **worker** rebuild is approval-gated and verified. Made checkable:

- Stage A matrix (steps 1-5) passes; every rebuild audited end to end (`proposal_auto_approved`, per-step events, plan hash, verify result in the Toolbelt journal and VictoriaLogs).
- At least one unattended Mimir or Kvasir rebuild verified with zero failed DNS answers beyond the budget, after two approval-gated ones.
- At least one worker rebuild approved and verified, with the manifest matching reality.
- Zero flapping (no target rebuilt twice in 24 h), breaker proven to trip and to need an operator.
- **10g3 gate (worker auto-rebuild is a later reviewed PR, not part of 10g):** N = 3 consecutive successful approval-gated worker rebuilds (one per worker, so each node's path is proven), a passing restore drill (offsite legs on burst plus PBS restore of a worker `/data`), and a clean single-instance-PV manifest on the node. Even then only workers whose manifest has no single-instance data.

## Risks

- **A mistaken T1 classification destroys data.** Mitigations: allow-list by name, deny list, last-chance PBS backup, plan-shape check, and the rule that only the registry (a reviewed PR) classifies.
- **The runner is a new privileged path on Frigg.** Mitigations: own unix user and socket, scoped PVE pool, no secret on disk beyond the AppRole secret-id, one in-flight rebuild, re-validation of everything it is sent. It is T3 infrastructure: changes to it are human-reviewed.
- **Frigg is a single control point.** If it dies the loop dies; the Gatus watcher on `do1` notices. Rebuilding Frigg stays human.
- **`do1` hosts the outside watcher.** While it is rebuilt the dead-man's switch has no listener and Gatus will report silence by design (independence is the point, so it cannot be told to stand down). The loop posts a pre-notice and the rebuild takes minutes, not hours.
- **Tailnet identity churn.** A rebuilt Tailscale LXC is a new machine record (decisions "Guest placement on a degraded node"); clients may need to re-select an exit node, and the stale record needs removal.
- **Urd shares a kernel with PBS and the canaries.** Faults stay inside guests; the PVE pool ACL stops the runner touching PBS.
- **Alert noise during a rebuild.** The originating alert is already firing; the Zabbix host will flap while converging. Accepted for now (open question about a Zabbix maintenance window).

## Build slices (each a PR, in this order; T0 first)

1. Runner skeleton + PVE pool + scoped token minting script with its negative test + `rebuild-plan` (T0) on canaries, plan-shape fixtures and tests. *Operator: terraform apply (pool/ACL), token mint, Vault AppRole/policy, Frigg playbook.*
2. Registry `rebuild:` + schema + lint + engine `steps/backend` + `rebuild-guest` / `rebuild-verify` / `start-guest` + converge/verify playbooks + Semaphore templates + the canary High trigger and `RB-GUEST-*` runbooks. Approval mode only. *Operator: semaphore apply.*
3. Bot cards (plan summary, manifest, backup age), `fault kill|destroy`, replay scenarios; the stage A matrix, then the policies enabled for canaries.
4. Stage B1: PBS last-chance backup, `neighbours_healthy` and VRRP-master guards, `pbs` restore drill automation; replicas approval-gated then policy.
5. Stage B2/B3: Tailscale (decision-dependent) and `do1` prerequisites.
6. Stage C: worker pre-flight manifest, drain/step-down identities, approval-gated rebuilds; the 10g3 gate bookkeeping in `/aiops report`.

## Operator steps (the ones Claude cannot do)

1. Decide the terraform-apply policy (option 2 above) and the CLAUDE.md/decisions amendment.
2. `terraform apply` (main checkout) for the PVE pool + ACL + token role, the narrow state IAM identity (`terraform/aws`, Bootstrap identity), the runner's Vault policy/AppRole (`terraform/vault`), and later the Semaphore templates.
3. Mint the PVE API token and mirror new secrets to 1Password (the mint scripts keep values out of the transcript; the 1P mirror is the operator's).
4. UCG: verify Frigg -> PVE API reach for the runner's uid (expected to exist; the Toolbelt already reads the PVE API from Frigg); add rules only if a new egress (DO API, S3) is blocked.
5. Hardware/host: none for canaries; Skuld de-risking stays an operator item.
6. Decisions listed in [`open-questions.md`](../../operations/open-questions.md) (Tailscale root-ticket, worker auto-rebuild gate N, Zabbix maintenance windows).

## Integration (engine + runner + playbooks, 2026-10-03)

The three slices were built in parallel against a shared contract and joined on one branch; the join found and fixed: (1) the verify playbook emits flat fields (`ssh_as_ansible`, `vlagent_active`, ...) while the engine expected a `checks` dict keyed by the registry's hyphenated condition names, so every real verify would have read as "missing": `rebuild_exec.checks_from_result` now maps them (and derives `agents-active`), tested against the registry's real canary conditions; (2) nothing gave the engine its runner: `aiops-toolbelt` gets `--rebuild-socket` (empty by default = rebuild actions refuse), and when set the unit also gets `AF_UNIX` and `SupplementaryGroups=aiops-rebuild-clients`. Confirmed compatible as built: the runner's `status.last` carries `plan_id` and `ok` for an apply, the registry sets the converge task `limit` to the target, and the converge playbook needs only `target` and an optional `plan_hash`. Still unwired: the PBS backup-age and VRRP-master read tools (an unknown master counts as the master, so a replica rebuild stays approval-gated), a worker manifest source (worker rebuilds stay blocked), and the live positive and negative tests of the PVE privilege set.

## Live state (2026-10-03, stage A infrastructure applied; nothing enabled to run)

Applied from the operator's local `main` (plan reviewed first, each plan purely additive): the PVE pool `aiops-canary`, user `aiops-rebuild@pve`, four roles, ACLs on `/pool/aiops-canary`, `/storage/local-lvm` (allocate), `/storage/local` (audit) and `/sdn/zones/localnetwork` (`SDN.Use`) and none on `/`, with the token secret written only to Vault (12 resources); the runner's Vault policy + AppRole (already present); the narrow S3 identity `rebuild-runner-state` (3 resources) with its key seeded into Vault `secret/ansible/aiops/rebuild/env` (field names and lengths verified, values never printed); the three Semaphore templates (`aiops-start-guest`, `aiops-rebuild-converge`, `aiops-rebuild-verify`). The runner is installed and running on Frigg (`/run/aiops-rebuild/runner.sock`, 0660, group `aiops-rebuild-clients`), the Toolbelt user can talk to it, and the canary guest-unreachable Zabbix trigger is imported. The Toolbelt and bot run the engine with every rebuild action still `planned`/`applied: false` and the `autonomy_rebuild` flag off.

**Verified live:** a `plan` for `canary-3` through the real runner (pool-scoped PVE token, narrow state identity) returns a valid single `replace` of exactly that container with its identity unchanged and no problems; nothing was applied.

**Found applying it:** `pool_id` forces replacement of an LXC and is never read back by the provider, so the first plan wanted to destroy all three canaries; the existing canaries were added to the pool with `pvesh set /pools/aiops-canary --vms ...` and `pool_id` is in the resource's `lifecycle.ignore_changes` ([known-issues/lxc-proxmox.md](../../known-issues/lxc-proxmox.md)).

**Next, in order:** (1) flip `start-guest`, `rebuild-guest` and `rebuild-verify` to `applied: true` (`rebuild-plan` stays planned: it runs on the runner, not Semaphore), approval-gated only; (2) kill `canary-3` with `scripts/canary/fault kill` and walk the diagnosis, the card, the approval, the rebuild and the verify; (3) a separate PR to enable the autonomous canary policy and `autonomy_rebuild`; (4) the replica stage only after the matrix and soak for stage A. The apply-path question (the runner is the one place Terraform runs unattended, on a clean `main` checkout with a pool-scoped token) is recorded as a decision below.

**Registry: runner-only actions (2026-10-03).** `rebuild-guest` also runs `rebuild-plan`, and the engine refuses a flow whose sub-actions are not applied. `rebuild-plan` runs entirely on the runner and has no Semaphore template, so the registry gained `semaphore.runner_only: true` (no template/playbook; `applied` means the runner is deployed; lint requires every step to be a runner step and forbids combining it with `planned`). With that, `start-guest`, `rebuild-guest`, `rebuild-verify` and `rebuild-plan` are applied (canary stage, approval-gated, no policy enabled, `autonomy_rebuild` off) and only `rebuild-worker` stays planned. The Toolbelt role now points at the runner socket by default.

**Proven end to end through the engine (2026-10-04, canary-3, proposal 25).** `scripts/canary/fault destroy` → diagnosis proposal path via chat → runner plan (a create, bound to the proposal) → operator approval → runner apply through the pool-scoped token (7.8 s) → converge through Semaphore (83 s) → the verify checklist (26 s) → `succeeded`, 117 s total, canary identity unchanged. Earlier attempts found, in order: the Semaphore `limit` field is dropped (`--limit` goes through `arguments`, #113/#114), a Semaphore read blip failed verify (reads now retry three times, a POST never does, #115), and two environment findings below. Eligibility also correctly refuses (`not-dead-or-broken`) a healthy canary.

**Test-harness notes.** (1) `pct destroy` removes the per-VMID ACL on `/vms/<vmid>`, so after a deliberate destroy the plan 403s until `terraform apply` in `terraform/proxmox/asgard-pools` recreates it (a genuinely dead guest keeps its ACL, so only the test destroy needs this). (2) Frigg's systemd-resolved, fed the fleet pair AdGuard VIP + UCG fallback, can stick to the UCG after a blip, and the UCG does not know `*.niflheim` names: the Toolbelt then fails with `semaphore unreachable: URLError`. `systemctl restart systemd-resolved` clears it; the durable fix is open (see open-questions). (3) The rate limits (class and per-target per day) count test applies; the test reset only clears `apply_started_at` on the test rows.

**Still off:** the autonomous canary policy and `autonomy_rebuild` (the next PR, after the 10f soak read), the replica stage and `rebuild-worker`.

## Header status history

*The status line this plan carried in its header, moved here verbatim when status consolidated into [`plans/README.md`](../README.md) (2026-10-10). It is a dated snapshot, not current status.*

> Status: **pure logic built (10g1, unit-tested), nothing deployed**: the eligibility rules, the plan checker, the worker data manifest and the registry `rebuild:` section exist as code and tests; the engine `steps`/`backend` support, flag, breaker and bot cards are built (10g2, faked in tests, see "As built"), but the rebuild runner, the Terraform pool and token, the playbooks and Semaphore templates, the runbooks and the bot cards are **not built** (see "As built so far").
