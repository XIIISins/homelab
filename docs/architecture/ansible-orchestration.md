<!-- docs/architecture/ansible-orchestration.md -->

# Ansible orchestration

How Ansible is organized, executed, and kept honest against drift. Captures the playbook-structure refactor + the Semaphore-driven push-scheduling loop. Sequenced as Phase 5h.3, immediately after Hermod (5h.2) because the drift-check loop is the first concrete producer of `alert`-tagged events into the notification hub.

## Playbook structure — per-host-group, not per-role

Current `ansible/playbooks/` mixes per-role and per-host-group playbooks (`-host` / `-hosts` / `-agents` suffixes inconsistently). The structural rule going forward:

**One playbook per inventory host-group.** Inside each playbook, the `roles:` list contains *every* role that group needs — baseline, hardening, service-specific roles, agents (vlagent, zabbix-agent). The playbook is the contract for "what state does this group of hosts hold."

| Pattern | Use |
|---------|-----|
| `asgard-<service>.yml` | Per-host-group entry point. Contains every role needed against that group. |
| `vlagent.yml`, `zabbix-agent.yml` | Cluster-wide *agent* rollouts. Cross-group. Kept separate to make agent deployment a deliberate one-shot rather than an implicit side-effect of every playbook run. |
| `site.yml` | Top-level orchestrator: `import_playbook:` every `asgard-*.yml` + cluster-wide agents. Single entry point for "converge everything." |

### Naming

`asgard-<service>.yml`. Drops the old `-host` / `-hosts` / `-agents` suffix confusion. The host-group affinity is implicit in the playbook name + the `hosts:` selector inside.

| Old | New |
|-----|-----|
| `asgard-k3s.yml` | `asgard-k3s.yml` (unchanged) |
| `postgres-host.yml` | `asgard-postgres.yml` |
| `haproxy-etcd-host.yml` | `asgard-haproxy-etcd.yml` |
| `adguard-host.yml` | `asgard-adguard.yml` |
| `tailscale-host.yml` | `asgard-tailscale.yml` |
| `factorio-host.yml` | `asgard-factorio.yml` |
| `zabbix-host.yml` | `asgard-zabbix.yml` |
| _(new, Phase 5h.2)_ | `asgard-hermod.yml` |
| `zabbix-agents.yml` | `zabbix-agent.yml` (singular — it's the agent rollout, not the agent fleet) |
| `vlagent-agents.yml` | `vlagent.yml` |
| `test-vault-lookup.yml` | **deleted** (throwaway proof-of-pattern; the AppRole lookup is now used in `roles/tailscale/` and the pattern is settled) |

### Multi-play files when `serial:` differs

A single playbook file can hold multiple `- name:` plays with different host selectors + serial settings. Use this when one *stage* needs ordered rollout (e.g. Patroni replicas first via `serial: 1`) and another *stage* of the same playbook should run in parallel (agent install on all members at once).

```yaml
# ansible/playbooks/asgard-postgres.yml (illustrative shape)
---
- name: Postgres stack — ordered (Patroni-safe)
  hosts: postgres_hosts
  serial: "{{ patroni_serial | default(1) }}"   # one node at a time
  become: true
  roles:
    - { role: baseline,         tags: baseline }
    - { role: postgres,         tags: postgres }
    - { role: patroni,          tags: patroni }
    - { role: postgres-common,  tags: postgres-common }
    - { role: hardening,        tags: hardening }

- name: Postgres agents — parallel
  hosts: postgres_hosts
  become: true
  roles:
    - { role: vlagent,          tags: vlagent }
    - { role: zabbix-agent,     tags: zabbix-agent }
```

The win: agents land via the same playbook (no separate `zabbix-agents.yml --limit postgres_hosts` invocation, no missed group when adding a new agent role) without paying the `serial: 1` tax on agent rollout.

The same pattern applies to K3s (CP-then-worker ordering vs parallel agent install) once the existing `asgard-k3s.yml` grows agent rollouts inline.

## Scheduling — Semaphore in asgard K3s

Push-mode scheduler driving drift-check + apply loops. Chosen over AWX based on resource footprint (~100 MB vs ~3 GB), single-binary Go runtime, and simpler operational story. Picked 2026-05-25; revisiting is cheap because the playbook structure is scheduler-agnostic — any future swap (AWX, Rundeck, K8s CronJob + raw ansible) doesn't need playbook changes.

### Where it runs

In asgard K3s, namespace `semaphore`. Single-replica StatefulSet, **Postgres backend on Patroni HAProxy VIP** (same pattern as Authentik / NetBox / Outline / Zabbix / Teamspeak — every other homelab K3s consumer is PG-backed). Drafts initially considered sqlite-on-PVC; pivoted to PG at deploy time for consistency + survives pod-restart-into-broken-PVC.

| Piece | Setup |
|-------|-------|
| Workload | StatefulSet `semaphore/semaphore`, 1 replica |
| Image | `semaphoreui/semaphore:v2.18.5-ansible2.16.5` (ansible-bundled variant) |
| Application state | PG on Patroni VIP `10.0.10.210:5432/semaphore` (per-service DB declared in `postgres_databases`, postgres-common provisions on leader) |
| Inventory cache | PVC `inventory-cache-semaphore-0`, `synology-csi-iscsi-retain`, 1 Gi. Cache survives pod restart — saves a ~30s NetBox round-trip every restart |
| Internal DNS | `semaphore.niflheim.xiiisins.com` via Traefik HTTPRoute on niflheim Gateway (internal-only — no midgard / apex alias, no CF tunnel) |
| Auth | Authentik OIDC (`semaphore-admins` group) + local break-glass admin |
| Vault integration | Semaphore "Vault" credential type for runtime AppRole lookups (configured post-deploy in UI / via TF if provider mature) |
| Ansible Vault password | Vault file credential, mounted/projected as `--vault-password-file` (configured post-deploy) |
| Secrets | ESO from `secret/k8s/semaphore/{postgres-password,app,oidc}` — split across `terraform/vault/main.tf` + `terraform/authentik/semaphore.tf` |

### Repo wiring

Semaphore project pointed at the homelab repo, branch `main`, working dir `ansible/`. Inventory and playbook paths resolved relative to that. The repo's `ansible/ansible.cfg` is the authoritative config (Semaphore inherits it).

### Templates

Three core Semaphore task templates cover the drift loop. Add per-service variants only when a specific service needs different cadence/serial.

| Template | Cadence | What it runs | Notify on |
|----------|---------|--------------|-----------|
| `refresh-netbox-inventory` | Cron `every 4h` | Refreshes the cached NetBox inventory by deleting the cache file and re-querying NetBox once | Failure only (NetBox down or auth broken) |
| `asgard-drift-check` | Cron `every 6h` | `ansible-playbook -i inventory/ site.yml --check --diff` | **Changes detected** → `tag: alert` to Hermod with the diff summary. Hard failure → `tag: alert`. Clean → no notification (audit trail in VL via vlagent) |
| `asgard-apply` | Manual | `ansible-playbook -i inventory/ site.yml` | Failure → `tag: critical` (an apply blew up — manual intervention needed). Success → no notification |

Per-service drift-check variants (e.g. `asgard-postgres-drift-check`) added later when a service needs a different cadence or has a known noisy-but-OK diff class that needs filtering before the alert fires.

## Inventory — NetBox dynamic + static fallback

Two inventory sources, both authoritative for their domain:

**Source 1: NetBox dynamic inventory** (primary). Plugin `netbox.netbox.nb_inventory` queries NetBox at runtime and projects hosts + groups from NetBox's metadata.

```yaml
# ansible/inventory/netbox.yml (Phase 5h.3 deliverable)
plugin: netbox.netbox.nb_inventory
api_endpoint: https://netbox.niflheim.xiiisins.com
token: "{{ lookup('community.hashi_vault.vault_kv2_get',
                  'ansible/netbox/inventory-token').secret.value }}"
validate_certs: true
group_by:
  - site         # niflheim
  - tag          # ansible:postgres, ansible:k3s-cp, etc.
  - role         # device role (proxmox-host, k3s-control-plane)
  - cluster      # niflheim, niflheim-pg
  - platform     # debian-13, rhel-9
cache: true
cache_plugin: jsonfile
cache_timeout: 14400        # 4 hours
cache_connection: /var/lib/semaphore/inventory-cache
cache_prefix: netbox_asgard_
```

Group membership comes from NetBox **tags**. Tags in NetBox follow the convention `ansible:<group>` (e.g. `ansible:postgres`, `ansible:k3s-cp`, `ansible:hermod`). The dynamic inventory picks up these tags via `group_by: tag` and exposes them as Ansible groups verbatim.

**Source 2: Static `hosts.yml`** (fallback + DR). Kept committed in `ansible/inventory/hosts.yml` permanently. Used when:
- NetBox is down (cache expired *and* NetBox API unreachable)
- Day-1 bootstrap (NetBox doesn't exist yet)
- Disaster recovery (cluster being rebuilt from cold)

Both sources merge transparently via Ansible's inventory plugin chain (`ansible/ansible.cfg` `inventory = inventory/netbox.yml, inventory/hosts.yml` — first-match wins per host). In practice NetBox is the source of truth during normal operations; `hosts.yml` exists as the emergency parachute.

### `group_vars/` and `host_vars/` overlay

File-based `group_vars/` and `host_vars/` continue to work unchanged. Ansible's variable precedence merges file-based vars on top of dynamic-inventory-supplied facts, so per-group knobs (`group_vars/postgres_hosts/vars.yml`) and per-host overrides (`host_vars/fulla/vars.yml`) remain authoritative for Ansible-specific configuration that doesn't belong in NetBox.

NetBox supplies: hostnames, IPs, group membership, platform/role tags, primary interface metadata.
Files supply: passwords (via Vault lookup), tunable knobs, per-environment overrides, secret material.

Rule of thumb: if the value answers "what is this host?" → NetBox. If it answers "how should Ansible configure it?" → file-based vars.

### Cache refresh

The 4 h `cache_timeout` is the steady-state TTL. Manual refresh (e.g. just added a new VM in NetBox and want it picked up *now*) goes via the `refresh-netbox-inventory` Semaphore template — runs `rm /var/lib/semaphore/inventory-cache/netbox_asgard_*` then a no-op `ansible -m ping localhost` to repopulate.

## Drift detection — `--check --diff` against `site.yml`

The drift-check template runs `ansible-playbook site.yml --check --diff` against the whole fleet on a 6 h cadence. Outputs:

- **Clean** (no `changed=`, no `failed=`): no Hermod notification. Audit trail in VL via vlagent picking up Semaphore's stdout/stderr → syslog.
- **Drift detected** (`changed=N` for any host): `tag: alert` POST to Hermod with title `"Drift detected: <N> tasks would change on <M> host(s)"`, body containing the host-task summary, `format: markdown`.
- **Hard failure** (`failed=N`): `tag: alert` POST with title `"Drift check failed: <playbook> on <host>"`, body containing the error message.

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

Semaphore's notification webhook config posts to Hermod at `http://hermod.niflheim.xiiisins.com:8000/notify/homelab` with the JSON schema specified in [notifications.md](../services/notifications.md).

## What stays out of scope (for Phase 5h.3)

- **Self-healing auto-apply.** Out by design — drift-check produces an alert and a human decides whether to converge. Removing the human is a separate decision with separate failure-mode implications.
- **PR-flow Ansible** (Atlantis-style). Already settled: not enabled. Solo dev, push-to-main matches existing Flux model. Decision row exists in `decisions.md`.
- **Jotunheim Ansible.** Out until jotunheim K3s exists (Phase 7). The same Semaphore project can pick up `jotunheim-*.yml` playbooks once written.
- **Per-service Vault-AppRole isolation.** Today everything uses the shared `ansible-awx` AppRole (or `ansible-local` for MacBook runs). Per-service AppRole isolation is a follow-up if/when the blast radius becomes a real concern.

## Implementation outline (Phase 5h.3 sub-phases)

1. **5h.3.a** — Rename existing playbooks per the table above. Delete `test-vault-lookup.yml`. Add multi-play stages where `serial:` differs. Add `site.yml`.
2. **5h.3.b** — Build NetBox-side: tag every device/VM with its `ansible:<group>` tag(s) via `terraform/netbox/` (additive change to the existing `tags` lists in `devices.tf` / `vms.tf`). Create a NetBox API token scoped to read-only inventory + write it to Vault at `secret/ansible/netbox/inventory-token`.
3. **5h.3.c** — Add `ansible/inventory/netbox.yml` dynamic inventory + the cache directory layout. Local validation: `ansible-inventory --graph` against both sources, confirm groups + memberships match `hosts.yml`'s expected shape.
4. **5h.3.d** — Deploy Semaphore in asgard K3s: `k8s/asgard/apps/semaphore/` (StatefulSet, PVC, Service, HTTPRoute on niflheim Gateway, ESO secrets, Authentik OIDC integration). Pinned chart or hand-rolled per the existing K3s pattern.
5. **5h.3.e** — Semaphore project setup (templated as far as possible via Semaphore's API + Terraform if a provider is available; manual UI otherwise): repo + key + Vault integration + the three core task templates with their cron schedules + the Hermod webhook config.
6. **5h.3.f** — Smoketest matrix: trigger drift-check manually against a clean fleet → no Hermod notification, VL has the log; mutate something on a host (`touch /etc/foo`) → drift-check detects → Hermod `alert` fires; manual apply converges → no notification; force a fail → Hermod `critical` fires.
7. **5h.3.g** — Documentation pass: canonical `--check` mode caveats section in this doc (above), AGH rewrite for `semaphore.niflheim.xiiisins.com → 10.0.20.10`, post-flight (build-sequence tick, decisions row, incident retro, CLAUDE.md gotchas). Per-role README updates folded into the design-doc catalogue rather than duplicated 17 times — scope decision recorded 2026-05-27.

## Open items at implementation time

- Semaphore version pin — pick at deploy time from then-current release notes (concrete pin per CLAUDE.md IaC pin policy).
- Inventory cache PVC vs emptyDir — PVC means cache survives pod restarts (better steady-state), emptyDir means restart = forced refresh (simpler, no PVC to manage). TBD.
- Semaphore Terraform provider — check whether [semaphoreui/terraform-provider-semaphore](https://github.com/semaphoreui/terraform-provider-semaphore) is mature enough to manage the project/templates/schedules declaratively. If yes, add `terraform/semaphore/`; if no, document the manual UI setup in a `docs/procedures/semaphore-bootstrap.md`.
- Whether `asgard-k3s.yml` should adopt the multi-play structure now (split agent rollout into its own play with parallelism) or leave as-is until adding a new agent forces the question.
