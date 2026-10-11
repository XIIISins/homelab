<!-- docs/architecture/ansible-orchestration.md -->

# Ansible orchestration

How Ansible is organized, executed, and kept honest against drift: the per-host-group playbook structure, the Semaphore push-scheduling loop, the NetBox dynamic inventory and the drift-check loop. Built in Phase 5h.3 (2026-05-27, [retro](../incidents/2026-05-27-5h3-semaphore-drift-check.md)); the drift loop is the first concrete producer of `alert`-tagged events into Hermod.

## Playbook structure — per-host-group, not per-role

**One playbook per inventory host-group.** Inside each playbook, the `roles:` list contains *every* role that group needs — baseline, hardening, service-specific roles, agents (vlagent, zabbix-agent). The playbook is the contract for "what state does this group of hosts hold."

| Pattern | Use |
|---------|-----|
| `asgard-<service>.yml` | Per-host-group entry point. Contains every role needed against that group. |
| `vlagent.yml`, `zabbix-agent.yml` | Cluster-wide *agent* rollouts. Cross-group. Kept separate to make agent deployment a deliberate one-shot rather than an implicit side-effect of every playbook run. |
| `site.yml` | Top-level orchestrator: `import_playbook:` every `asgard-*.yml` + cluster-wide agents. Single entry point for "converge everything." |

### Naming

`asgard-<service>.yml`; the host-group affinity is implicit in the name and the `hosts:` selector inside. Current set: `asgard-{k3s,adguard,tailscale,postgres,haproxy-etcd,factorio,hermod,gna,ratatoskr,jellyfin,zabbix,proxmox,pbs,control}.yml`, plus the cross-group `vlagent.yml`, `zabbix-agent.yml` (the agent rollout, singular) and `fleet-agents.yml`. `site.yml` imports them (after a preflight and `zabbix-host-groups.yml`), with `platform-version-drift.yml` right after `asgard-k3s.yml`. Non-prod: `site-nonprod.yml` for the canary pool. Older docs and retros use the former `*-host.yml` / `*-hosts.yml` / `*-agents.yml` names; `test-vault-lookup.yml` was a throwaway proof-of-pattern and is deleted (the AppRole lookup pattern is settled in `roles/tailscale/`).

`site.yml`'s preflight asserts the inventory has a sane host count (`site_inventory_min_hosts`, default 20), so a cold NetBox cache plus a failed live rebuild fails loud instead of silently skipping every play.

### Multi-play files when `serial:` differs

A single playbook file can hold multiple `- name:` plays with different host selectors + serial settings. Use this when one *stage* needs ordered rollout (e.g. Patroni replicas first via `serial: 1`) and another *stage* of the same playbook should run in parallel (agent install on all members at once).

```yaml
# ansible/playbooks/asgard-postgres.yml (shape, simplified)
---
- name: Postgres stack — ordered (Patroni-safe)
  hosts: postgres
  serial: "{{ patroni_serial | default(1) }}"   # one node at a time
  become: true
  roles:
    - { role: baseline,         tags: baseline }
    - { role: postgres,         tags: postgres }
    - { role: patroni,          tags: patroni }
    - { role: postgres-common,  tags: postgres-common }
    - { role: hardening,        tags: hardening }

- name: Postgres agents — parallel
  hosts: postgres
  become: true
  roles:
    - { role: vlagent,          tags: vlagent }
    - { role: zabbix-agent,     tags: zabbix-agent }
```

The win: agents land via the same playbook (no separate `zabbix-agent.yml --limit postgres` invocation, no missed group when adding a new agent role) without paying the `serial: 1` tax on agent rollout.

`asgard-k3s.yml` has not adopted the split yet (CP-then-worker ordering vs parallel agent install); that waits until a new agent forces the question.

## Scheduling — Semaphore in asgard K3s

Push-mode scheduler driving drift-check + apply loops. Chosen over AWX based on resource footprint (~100 MB vs ~3 GB), single-binary Go runtime, and simpler operational story. Picked 2026-05-25 (decision row "Ansible scheduler — Semaphore in asgard K3s (over AWX)"); revisiting is cheap because the playbook structure is scheduler-agnostic — any future swap (AWX, Rundeck, K8s CronJob + raw ansible) doesn't need playbook changes.

### Where it runs

In asgard K3s, namespace `semaphore`. Single-replica StatefulSet, **Postgres backend on Patroni HAProxy VIP** (same pattern as Authentik / NetBox / Outline / Zabbix / Teamspeak — every other homelab K3s consumer is PG-backed). Chosen over sqlite-on-PVC for consistency and so a pod restart can't land on a broken PVC.

| Piece | Setup |
|-------|-------|
| Workload | StatefulSet `semaphore/semaphore`, 1 replica |
| Image | `ghcr.io/xiiisins/semaphore-homelab:v2.18.5-ansible2.16.5-r3` — custom (stock `semaphoreui` ansible-bundled base + pinned Galaxy collections + runtime pip deps baked; see "Custom image" below) |
| Application state | PG on Patroni VIP `10.0.10.210:5432/semaphore` (per-service DB declared in `postgres_databases`, postgres-common provisions on leader) |
| Inventory cache | `emptyDir` at `~/.cache/ansible/netbox-inventory` (a PVC would cost an iSCSI LUN against the DS223J cap). A pod restart empties it; the `refresh-netbox-inventory` template or the next consumer run rebuilds it in ~30 s |
| Internal DNS | `semaphore.niflheim.xiiisins.com` via Traefik HTTPRoute on niflheim Gateway (internal-only — no midgard / apex alias, no CF tunnel) |
| Auth | Authentik OIDC (`semaphore-admins` group) + local break-glass admin |
| Vault integration | The `ansible-awx` AppRole (role id and SecretID injected into the task environment, not a Semaphore credential type); the SecretID is never in Terraform state (rotation: `rotate-semaphore-approle`) |
| Ansible Vault password | A Semaphore `ansible-vault-password` key, used as `--vault-password-file` |
| Secrets | ESO from `secret/k8s/semaphore/{postgres-password,app,oidc}` — split across `terraform/vault/main.tf` + `terraform/authentik/semaphore.tf` |

### Repo wiring

Semaphore project pointed at the homelab repo, branch `main`, working dir `ansible/`. Inventory and playbook paths resolved relative to that. The repo's `ansible/ansible.cfg` is the authoritative config (Semaphore inherits it). The project (`asgard`, which serializes runs: `max_parallel_tasks = 1`), its keys, repository, inventory, environment and every template and schedule are declared in `terraform/semaphore/` (the `semaphoreui` provider); a separate `aiops` project holds the action executor ([`aiops-actions.md`](../procedures/aiops-actions.md)).

### Custom image (collection pins)

Semaphore runs a **custom image** (`docker/semaphore/Dockerfile` → `ghcr.io/xiiisins/semaphore-homelab`, built by `.github/workflows/build-semaphore-image.yml`), not the stock `semaphoreui` image. The stock image bundles its own Galaxy collection set and Semaphore never runs `ansible-galaxy collection install` — so the pins in `ansible/requirements.yml` (which the MacBook/Frigg controllers galaxy-install and honor) **never reached the Semaphore runtime**; every play silently ran on the older bundled collections. This surfaced when the `zabbix-agent` `register-host` task (4.x `community.zabbix` httpapi auth) passed from Frigg but failed from Semaphore, which had bundled 2.3.1. The custom image installs the pinned collections with `--force` directly into the ansible venv's `site-packages/ansible_collections` (overwriting the bundle) + bakes the runtime pip deps (`hvac`/`psycopg2-binary`/`pytz`, formerly a pod-start `pip install`). Site-packages is the load-bearing target: Semaphore runs tasks with the repo's `collections_path = collections` (empty) ansible.cfg and builds the task env itself, so a pod-spec `ANSIBLE_COLLECTIONS_PATH` never reaches the task — the only channel is Ansible's import finder scanning `sys.path`, which is the venv site-packages (exactly how MacBook/Frigg resolve 4.2.0). **Bump discipline:** changing `ansible/requirements.yml` (or the base image) means bumping `BASE_IMAGE` (Dockerfile) + `IMAGE_TAG` rN (workflow) + the StatefulSet image tag in one commit. First GHCR-hosted custom image in the homelab — the Dockerfile + workflow pattern is reusable for any future one. Gotcha and diagnostic: [`known-issues/semaphore.md`](../known-issues/semaphore.md).

### Templates

Declared in `terraform/semaphore/templates.tf` (the authority; this is the shape). The drift/apply/agents templates run thin *wrapper* playbooks (`drift-check.yml`, `apply.yml`, `fleet-agents.yml`) so the `hermod_summary` callback, which infers the mode from the first playbook's filename, can tell same-file reuse apart.

| Template | Cadence | What it runs | Notify on |
|----------|---------|--------------|-----------|
| `refresh-netbox-inventory` | cron every 4 h | `ansible/scripts/refresh-netbox-inventory.sh`: wipe the cache and rebuild it, retrying NetBox 5xx blips until the result has a sane host count | Failure only |
| `asgard-drift-check` | cron every 6 h | `site.yml --check --diff` | **Changes detected** → `alert` with the diff summary; hard failure → `alert`; clean → nothing (log in VL) |
| `asgard-apply` | manual | `site.yml` | Failure → `critical`; success → nothing |
| `asgard-os-updates` | manual | `os-updates.yml`: fleet OS patch and reboot, `serial: 1` per quorum group | |
| `asgard-fleet-agents` | cron daily | `fleet-agents.yml`: vlagent + zabbix-agent reconverge across every host (defence-in-depth over the per-group roles) | Failure → `critical` |
| `asgard-nonprod-drift-check` / `-apply` | cron daily / manual | `site-nonprod.yml` (canary pool) | Failures capped at `info`, title prefixed `[non-prod]` |
| `infra-health-check` | cron every 12 h | `infra-health-check.yml` (`hosts: localhost` in the pod): Cloudflare-token validity, served-cert expiry ×3 zones, Patroni `/cluster` + etcd `/health` quorum, PBS failed tasks | The playbook posts `critical`/`alert` itself; clean → nothing. (`hermod_summary` no-ops for non-drift/apply wrappers, so no double post.) |
| `pbs-restore-test` | cron weekly | Restore the newest backup of a canary and a rotating LXC into a scratch CT, verify, destroy ([procedure](../procedures/pbs-restore-test.md)) | The playbook posts its own result |
| `aiops-*` | manual (`aiops-code-deploy` every 15 min) | The action registry's playbooks ([`aiops-actions.md`](../procedures/aiops-actions.md)) | |

Per-service drift-check variants are added only when a service needs a different cadence or has a known noisy-but-OK diff class to filter before the alert fires.

The `infra-health-check` prober is the **active**-check complement to the drift loop's config-check: drift-check confirms Git == reality; the prober confirms live edge/app state (a token's validity, a served cert's expiry, cluster quorum, a backup's outcome) that no converge would catch. Plan and validation: [`1.0-stabilization.md`](../plans/done/1.0-stabilization.md), [`s4-observability-validation.md`](../procedures/s4-observability-validation.md).

## Inventory — NetBox dynamic + static fallback

Two inventory sources, both authoritative for their domain:

**Source 1: NetBox dynamic inventory** (primary). `ansible/inventory/netbox.yml` uses `netbox.netbox.nb_inventory` (enabled explicitly in `ansible.cfg`, otherwise the file is silently skipped as unparseable YAML) with the read-only token at `ansible/netbox/inventory-token`:

- Filters: active, with a primary IP, excluding firewall and NAS devices. `ansible_host` is composed from the primary IP.
- Groups: NetBox tags `ansible:<group>` (the colon is stripped from the slug, so the tag `ansible:patroni` has slug `ansiblepatroni`) become groups via `keyed_groups`, plus computed `k3s` and `asgard_lxcs` (VMID 1100–1199) groups. Tags are declared in `terraform/netbox/tags.tf` and assigned in `devices.tf` / `vms.tf`.
- Cache: `jsonfile` at `~/.cache/ansible/netbox-inventory`, prefix `netbox_asgard_`, **24 h TTL** with a 4 h refresh job, so consumers always hit a warm cache between refreshes. (A TTL equal to the refresh cadence left a race where a consumer between wipe and repopulate rebuilt cold against NetBox.)

**Source 2: Static `hosts.yml`** (fallback + DR). Kept committed in `ansible/inventory/hosts.yml` permanently. Used when:
- NetBox is down (cache expired *and* NetBox API unreachable)
- Day-1 bootstrap (NetBox doesn't exist yet)
- Disaster recovery (cluster being rebuilt from cold)

Both sources merge transparently via Ansible's inventory plugin chain (`ansible.cfg`: `inventory = inventory/netbox.yml, inventory/hosts.yml`). In practice NetBox is the source of truth during normal operations; `hosts.yml` exists as the emergency parachute.

### `group_vars/` and `host_vars/` overlay

File-based `group_vars/` and `host_vars/` continue to work unchanged. Ansible's variable precedence merges file-based vars on top of dynamic-inventory-supplied facts, so per-group knobs (`group_vars/postgres.yml`) and per-host overrides (`host_vars/<host>.yml`) remain authoritative for Ansible-specific configuration that doesn't belong in NetBox.

NetBox supplies: hostnames, IPs, group membership, platform/role tags, primary interface metadata.
Files supply: passwords (via Vault lookup), tunable knobs, per-environment overrides, secret material.

Rule of thumb: if the value answers "what is this host?" → NetBox. If it answers "how should Ansible configure it?" → file-based vars.

### Cache refresh

The `refresh-netbox-inventory` template (every 4 h, or on demand when a VM was just added in NetBox) runs `ansible/scripts/refresh-netbox-inventory.sh`, which rebuilds the cache with retries and refuses a result below the minimum host count (a malformed response can otherwise fall through to an implicit-localhost inventory, as happened in task 27). A pod restart wipes the `emptyDir`; `site.yml`'s preflight is the backstop if NetBox is also blipping.

## Drift detection — `--check --diff` against `site.yml`

The drift-check template runs `ansible-playbook site.yml --check --diff` against the whole fleet on a 6 h cadence. Outputs:

- **Clean** (no `changed=`, no `failed=`): no Hermod notification. Audit trail in VL via vlagent picking up Semaphore's stdout/stderr → syslog.
- **Drift detected** (`changed=N` for any host): `tag: alert` POST to Hermod with title `"Drift detected: <N> tasks would change on <M> host(s)"`, body containing the host-task summary, `format: markdown`.
- **Hard failure** (`failed=N`): `tag: alert` POST with title `"Drift check failed: <playbook> on <host>"`, body containing the error message.

**Platform version drift (K3s + Calico vs git)** — `playbooks/platform-version-drift.yml` is imported into `site.yml` right after `asgard-k3s.yml` with `platform_drift_check_only: true`, so it runs only under `--check` (the drift-check template) and is skipped by `asgard-apply`. It compares `k3s --version` per node and the live Calico `status.calicoVersion` + IPPool CIDR against `k3s_version` / `calico_version` / `k3s_pod_cidr` in `roles/k3s/defaults/main.yml`. A mismatch is an `assert` **failure** → the existing "Drift check failed" Hermod alert, whose body carries the fix (`run playbooks/k3s-upgrade.yml` / `calico-upgrade.yml`). Those versions are never auto-converged — drift here means "a git bump hasn't been rolled out yet" or "something moved the cluster outside git". Standalone: `ansible-playbook playbooks/platform-version-drift.yml` (always runs; `--tags platform-drift` selects it inside `site.yml`).

**`--check` mode caveats** — patterns that emerged during the 5h.3 baselining sweep (clean drift-check across the asgard fleet, 2026-05-27):

- `--check` doesn't validate file ownership/mode, only existence. Bugs that only surface on real apply are invisible to drift-check.
- Modules with side-effects outside Ansible's awareness (`uri:` POST, `command:`, `shell:`, package downloads, `unarchive` of remote URLs) are no-ops in `--check` and would error if attempted against not-yet-bootstrapped state. Roles guard these by class:
  - **Download + extract** (AGH tarball, kubeconfig fetch, AppriseAPI tarball, Factorio tarball) — skipped when `ansible_check_mode` and the destination is absent, so first-time drift-check against a not-yet-bootstrapped host doesn't fail loud.
  - **Service enable + start** — skipped in `--check` for roles where the unit file is rendered by the same play (vlagent, hermod-api, etc.) — handler would race the render.
  - **Mutating API calls** that read-back to compare state (`sftpgo` POST-then-read, Patroni `/cluster` REST polling, NetBox provisioning, postgres-common `CREATE ROLE / CREATE DATABASE`) are gated either by `check_mode: false` on the read-only probe or by skipping the mutating task entirely on `--check`.
  - **Leader-gated provisioning** (postgres-common databases, Patroni adoption) skips under `--check` — leader discovery itself depends on a live cluster the check-mode invocation may not have warmed.
  - **Fresh-install-only paths** (sftpgo daemon stop, factorio reconcile-on-bootstrap) gate on `is_fresh_install` AND `not ansible_check_mode`.
- The drift-check Semaphore template wraps `ansible-playbook` in a preflight assert that demands `--check` is present + that `ansible_check_mode` is true at play-start — prevents an accidental `--check`-less re-run from mutating the fleet.
- `roles/sftpgo/` still has structural `check_mode: false` on the POST-then-read pattern (documented in [open-questions.md](../operations/open-questions.md)).

When false-positive cycles surface in `tag: alert` notifications, the fix lives in the role, not the drift-check template. Per-role check-mode handling is intentionally NOT documented inline in each role's README — the patterns are uniform across roles and the catalogue above is the canonical reference; role-level READMEs note check-mode behavior only when the role does something genuinely role-specific (currently none).

### What drift-check reports vs what apply does

`asgard-drift-check` only reports. `asgard-apply` is the **manual** template — operator-triggered when drift is investigated and the right action is "converge." There is deliberately no scheduled auto-apply; drift-check + Hermod alert + operator decision is the loop.

## Integration with Hermod

Drift events tagged per the [notification design doc](../services/notifications.md):

| Event | Apprise tag | Notes |
|-------|------------|-------|
| Scheduled drift-check: clean | _(none — only logs to VL)_ | Routine; queryable post-hoc |
| Scheduled drift-check: changes detected | `alert` | Operator should investigate within business-day |
| Scheduled drift-check: hard failure (Ansible error, not "would change") | `alert` | Same response window |
| Manual apply: success | _(none — only logs to VL)_ | Operator already knows it ran |
| Manual apply: failure | `critical` | Apply blew up — operator needs to act now |
| Inventory refresh failure | `alert` | NetBox unreachable; cache will age out at 4 h |

Semaphore's notification webhook config posts to Hermod at `http://hermod.niflheim.xiiisins.com/notify/<config-key>` (the `HERMOD_URL` environment, built from Vault) with the JSON schema specified in [notifications.md](../services/notifications.md).

## Out of scope

- **Scheduled auto-apply.** There is none: drift-check alerts and a human decides whether to converge. Autonomous healing is the separate, guarded Phase 10f (T1 actions on the canary pool first; [`aiops-autonomy.md`](../procedures/aiops-autonomy.md)), and fleet-wide auto-convergence is a direction, not a decision.
- **PR-flow Ansible** (Atlantis-style). Already settled: not enabled. Solo dev, push-to-main matches existing Flux model. Decision row exists in `decisions.md`.
- **Jotunheim Ansible.** Moot: Phase 7 (jotunheim K3s) was dropped 2026-10-05 for lack of resource capacity.
- **Per-service Vault-AppRole isolation.** Today everything uses the shared `ansible-awx` AppRole (or `ansible-local` for MacBook runs). Per-service AppRole isolation is a follow-up if/when the blast radius becomes a real concern.

## Not done

- `asgard-k3s.yml` multi-play split (above).
- Per-service Vault AppRole isolation (above).
