<!-- docs/incidents/2026-05-27-5h3-semaphore-drift-check.md -->

# 2026-05-27 — Phase 5h.3 close: Semaphore + drift-check baseline

End-to-end Phase 5h.3 deploy and the drift-check shakeout that followed. Semaphore landed as the asgard Ansible scheduler, the playbook tree was restructured per-host-group, NetBox dynamic inventory replaced the static-only mode, and a fleet-wide `ansible-playbook site.yml --check --diff` was driven to **zero `changed=` across all hosts** — the steady-state baseline drift-check now alerts off. 66 commits across the phase; ~20 of them are the `--check` shakeout, the rest are Semaphore wiring + inventory plumbing + the os-updates role split.

## Outcome

- **Semaphore** v2.x StatefulSet in `semaphore` namespace, **Postgres backend via Patroni VIP** `10.0.10.210` (DB `semaphore`, password at `secret/k8s/semaphore/postgres-password`), inventory-cache on emptyDir (Synology DS223J 10/10 LUN-cap workaround — same class as Outline+Redis earlier this week), Authentik OIDC, internal-only HTTPRoute at `semaphore.niflheim.xiiisins.com`.
- **Three core templates live and scheduled:** `refresh-netbox-inventory` (cron 4h), `asgard-drift-check` (cron 6h, `--check --diff` against `site.yml`), `asgard-apply` (manual). Semaphore webhook posts to Hermod (`tag: alert` on drift/failure, `critical` on apply-fail, none on clean). Hermod-summary callback plugin parses `PLAY RECAP` and ships a compact markdown body with per-host counts.
- **Playbook tree restructured.** Every group now has one `asgard-<group>.yml` (postgres / haproxy-etcd / k3s / adguard / tailscale / gameserver / apprise / zabbix). Cluster-wide agent rollouts (`vlagent.yml`, `zabbix-agent.yml`) stay as their own playbooks. `site.yml` `import_playbook`s the lot. The old `*-host.yml` / `*-hosts.yml` / `*-agents.yml` mix is gone. Multi-play files (where one stage wants `serial: 1` and another wants parallel) deferred — single-play files cover current needs.
- **NetBox dynamic inventory** is the primary source. `netbox.netbox.nb_inventory` plugin, `group_by: site, tag, role, cluster, platform`, jsonfile cache 24h TTL on the Semaphore pod's emptyDir, refresh template rebuilds the cache out-of-band of any consumer. `hosts.yml` retained as DR fallback (cluster-rebuild from cold, NetBox-down).
- **OS patching split out** of the baseline role into a new `os-updates` role + `os-updates.yml` playbook (cluster-wide, per-group `serial: 1` for quorum-sensitive groups, Proxmox hosts excluded). Re-running converge playbooks no longer pulls OS updates implicitly — that's a deliberate Semaphore template now.
- **Drift-check baseline is clean.** Fleet-wide `site.yml --check --diff` returns 0 `changed=` and 0 `failed=` on every host. From here on, an `alert` in the Hermod channel means "something on a host disagrees with Git" — the signal is unambiguous.

## Pre-flight context

- Hermod (5h.2) closed 2026-05-26 with both producer paths (Zabbix mediatype + Patroni callback) live. Drift-check is now the third concrete `alert`-tag producer.
- Patroni cluster on Idunn leader at deploy start; PG backend for Semaphore lives there.
- NetBox 4.6.1 live since 5i (2026-05-24); TF→NetBox standing pattern from 5i.3 means every device/VM in `terraform/netbox/{devices,vms}.tf` already carries its `ansible:<group>` tags.
- ESO + Vault KV stable since Phase 4.
- AppRole rotation discipline established 2026-05-27 after a transcript-leak rotation event ([commit ef4a185](../../commit/ef4a185)) — Semaphore is on the `ansible-awx` AppRole; rotation uses the `rotate-semaphore-approle` fish helper.

## Key findings

### Semaphore K8s deploy — pod-start plugin install pattern

Semaphore's stock image has Ansible but lacks several plugins the homelab playbooks need. Mounting custom images is heavier than the value; the working pattern is **pip install at pod start** via an init script:

- `hvac` — `community.hashi_vault` lookups against Vault.
- `psycopg2-binary` — `community.postgresql.*` modules (PG provisioning).
- `pytz` — `netbox.netbox.nb_inventory` dependency (date handling on cached records).

All three installed via `pip install --quiet --no-cache-dir` in the StatefulSet's `command:` wrapper. ~30s added to pod start; acceptable against rebuild-image-on-every-collection-bump. Documented as the canonical pattern in the Semaphore manifest.

### Semaphore config schema discovery — undocumented env-var / JSON shape

Several config knobs that the docs describe one way actually wire up via a different env-var or JSON shape inside the deployed binary. Hit and corrected in order:

- **DB SSL goes via `SEMAPHORE_DB_OPTIONS` JSON, not `SEMAPHORE_DB_SSL_MODE`** — the env-var the docs describe is silently dropped; `SEMAPHORE_DB_OPTIONS='{"sslmode":"require"}'` is the actual switch.
- **`enableServiceLinks: false` required on the pod spec** — Semaphore reads env vars whose names collide with K8s' auto-injected `<SERVICE>_PORT=tcp://...` shape, e.g. `SEMAPHORE_PORT`. Without `enableServiceLinks: false`, K8s' injection wins and Semaphore starts listening on garbage.
- **`vaults:` block in `config.json` nests `password.vault_key_id`** — flat `vault_key_id` at top level silently ignored.
- **`ANSIBLE_HASHI_VAULT_ADDR`** is the env var the community.hashi_vault collection reads — NOT `ANSIBLE_HASHI_VAULT_URL` (which doesn't exist).
- **Vault password file path goes through env var, not `ansible.cfg`** — Semaphore mounts vault-pass into a per-job tmpdir; the path differs per template. `ANSIBLE_VAULT_PASSWORD_FILE` env override works; `ansible.cfg`'s static path doesn't.
- **`config.json` mounted directly as a Secret volume** — Semaphore reads it on startup; updates require pod restart (chart's `--config` arg doesn't watchdog).

All discovered by chasing pod-start errors against an otherwise-running Semaphore. Each is one or two lines in the manifest; the schema discovery is the part that consumed the time.

### NetBox dynamic inventory — silent success on cold cache + NetBox blip

The most subtle finding of the phase, generalised into a **system-wide rule** in CLAUDE.md "NetBox" section. Failure chain:

1. `refresh-netbox-inventory` template wipes the jsonfile cache (the refresh IS a rebuild from live state).
2. The next consumer template (drift-check / apply) starts before the next refresh tick.
3. The `nb_inventory` plugin attempts a live fetch.
4. NetBox returns a transient HTTP 500 (`OperationalError` — same Patroni connection-pool blip class first surfaced 2026-05-25 during TF apply refresh-bursts).
5. The plugin parses 0 hosts (instead of failing loud).
6. Every play reports `skipping: no hosts matched`.
7. Ansible exits 0.
8. **Semaphore reports `success`.**

Triggered task 27 on 2026-05-27. The drift-check ran, alerted nothing, and was completely a no-op against zero hosts — and the channel was silent because the success criterion was "ansible exited 0," not "ansible touched > N hosts."

**Three-layer fix landed:**

1. **Refresh script retries.** `refresh-netbox-inventory.sh` now retries up to 10×30s on the live fetch — transient 500s don't fail the refresh.
2. **Cache TTL > refresh cadence.** Bumped to 24h (refresh runs every 4h) so consumers always hit a warm cache in steady state. Cold-cache fetch only happens when the refresh ran AND the plugin's cache miss races a NetBox blip — bounded.
3. **Preflight host-count assert in `site.yml`.** `assert: that: groups['all'] | length >= 20` fails loud if the inventory came back empty or near-empty. The number is the structural minimum (3 K3s CPs + 3 workers + 3 AGH + 3 Tailscale + 3 PG + 3 HAProxy/etcd + a handful of LXCs ≫ 20).

Generalisable lesson encoded in CLAUDE.md: dynamic-inventory plugins that fall through to implicit-localhost on failure turn into silent no-ops in any CI runner whose only success criterion is the exit code. Pre-task host-count assertion is the structural defence. The retry + TTL together fix the immediate path; the assert catches whatever else slips through.

### Inventory plumbing — name collisions and lookup paths

- **Inventory file isolated from `group_vars/`** ([commit 04803eb](../../commit/04803eb)). The `nb_inventory` plugin walks `group_vars/` at parse time and was decrypting Ansible Vault on every Semaphore call — slow + brittle when the vault-pass file path is per-template. Moved `inventory/netbox.yml` to a parallel dir so the plugin doesn't see `group_vars/all/vault.yml`.
- **netbox-injected `tags` → `netbox_tags`** ([commit 3571d70](../../commit/3571d70)). NetBox's `tags` attribute on devices/VMs collided with the standard Ansible `tags` semantics on plays/tasks. Renamed to `netbox_tags` via `keyed_groups` rewrite; the original `tags` namespace is now safe for play-level use.
- **Tag-rename keyed_groups break + revert** ([commit 055599e](../../commit/055599e)). First attempt nulled `tags='[]'` to force the rename; that broke `keyed_groups` entirely (no source data to key on). Reverted to letting both names co-exist with the rename happening downstream.
- **Group renames** ([commit 5fa891f](../../commit/5fa891f)). `factorio` → `gameserver`, `hermod` → `apprise` — the NetBox tags should describe the role-class (gameserver, apprise, monitoring), not the specific identity (factorio, hermod) so adding a second gameserver later is a tag-add, not a group-rename.
- **VM map key case** ([commit 4ccd4b2](../../commit/4ccd4b2)). 6 VM map keys in `terraform/netbox/vms.tf` were Title-Case; inventory consumers expect lowercase to match `hosts.yml` convention. Lowercased.
- **Urd → urd** ([commit 3ec81c1](../../commit/3ec81c1)). Same class, but for the physical host — `terraform/netbox/devices.tf` had `Urd` where inventory expected `urd`.
- **UCG-Ultra exclusion** ([commit 8415c8e](../../commit/8415c8e)). The UCG-Ultra is a NetBox device but it's not Ansible-managed (it has no SSH, the management plane is the UniFi controller). Excluded via inventory filter so drift-check doesn't try to gather facts against it.

### Drift-check `--check` mode shakeout — 20 commits across 19 roles

The detailed catalogue of patterns is in [`docs/architecture/ansible-orchestration.md`](../architecture/ansible-orchestration.md) "Drift detection — `--check --diff` against `site.yml`" — copied below in summary:

- **Download / extract** (AGH tarball, kubeconfig fetch, AppriseAPI tarball, Factorio tarball) — skipped under `ansible_check_mode` when the destination is absent. Otherwise drift-check against a not-yet-bootstrapped host fails loud trying to fetch a remote URL.
- **Service enable + start** — skipped under `--check` for roles where the unit file is rendered by the same play (vlagent, hermod-api, etc.) — handler races the render in check mode.
- **Mutating API calls that read-back to compare state** (sftpgo POST-then-read, Patroni `/cluster` REST, NetBox provisioning, postgres-common `CREATE ROLE`) — gated either by `check_mode: false` on the probe or by skipping the mutating task entirely.
- **Leader-gated provisioning** (postgres-common databases, Patroni adoption) — skipped under `--check` because leader discovery itself depends on a live cluster.
- **Fresh-install-only paths** (sftpgo daemon stop, factorio reconcile-on-bootstrap) — gated on `is_fresh_install AND not ansible_check_mode`.

The drift-check Semaphore template's wrapper preflight-asserts `ansible_check_mode == true` and fails loud otherwise — prevents an accidental `--check`-less re-run of the same template from mutating the fleet ([commit 05762d3](../../commit/05762d3)).

The pattern is uniform across roles. Per-role READMEs deliberately do NOT duplicate the catalogue — scope decision recorded 2026-05-27.

### Existing CLAUDE.md gotchas reinforced

The shakeout exercised several existing rules that previously had thin evidence:

- **`include_tasks` doesn't propagate `--tags`** — re-affirmed during postgres-common provisioning re-runs.
- **Strict-mode boolean conditionals need JSON `-e`** — re-affirmed against `patroni_is_leader`.
- **`tasks_from:` silently ignored in the play-level `roles:` block** — the postgres-common role split from Phase 5i covers this; no new instances.

### Secret handling — two related rotations during the phase

Documented in their own retros and reinforced in CLAUDE.md:

- **Vault `kv patch field=-` stdin form is unreliable** ([commit ef4a185](../../commit/ef4a185)). Surfaced during AppRole rotation when Semaphore drift-checks started failing with `permission denied` on `/v1/auth/approle/login`. Diagnostic: hash-compare `vault kv get -field=...` against canonical source. Defence-in-depth pattern in `rotate-semaphore-approle`.
- **AWS auto-unseal IAM access key rotation** ([commit e77c7f1](../../commit/e77c7f1)). Transcript leak; operator rotated.

Neither was a 5h.3 failure per se — both were operational hygiene events that happened during the same window and are encoded as rules.

### OS patching split

Surfaced as a scope-creep concern during the playbook restructure. `baseline` was running `apt-get update + upgrade` on every converge, which (a) made converge runs minutes-slow, (b) meant drift-check would have to handle the "package available but not installed" cycle every time. Splitting into a separate `os-updates` role + playbook ([commit 20c7645](../../commit/20c7645)) is cleaner: `baseline` is pure config-idempotency now, and OS patching runs on its own Semaphore template at its own cadence (per-group `serial: 1` for quorum-sensitive groups, Proxmox hosts deliberately excluded — host reboots take VMs/LXCs with them, separate manual procedure).

CLAUDE.md "Ansible / roles" section already encodes the rule; this phase is where it actually landed.

## Findings → CLAUDE.md cross-references

| Finding | Where it landed |
|---------|-----------------|
| NetBox dynamic-inventory silent-success | CLAUDE.md "NetBox" section ([commit 781a5b2](../../commit/781a5b2)) |
| Semaphore uses `ansible-awx` AppRole (NOT `ansible-local`) | Memory `feedback_semaphore_uses_ansible_awx.md` |
| `vault kv patch field=-` unreliable | Memory `feedback_vault_kv_patch_stdin_unreliable.md` + CLAUDE.md "Vault" |
| `--check` mode caveats catalogue | `docs/architecture/ansible-orchestration.md` (single canonical reference) |
| `baseline` no longer patches OS — see `os-updates` role | CLAUDE.md "Ansible / roles" ([commit 7605d2a](../../commit/7605d2a)) |

## What stays open

- **Multi-play files** — none introduced yet. Will land the first time a single `asgard-<group>.yml` has both a `serial: 1` stage (e.g. PG/Patroni) and a parallel stage (e.g. agent install) that benefit from being separate. `asgard-postgres.yml` is the candidate; deferred until the parallel-friendly tax becomes visible.
- **`ansible-playbook` concurrency lock** (mentioned in CLAUDE.md "Mutating operations — what runs where"). Worktree isolation handles git state but not live infra; the `flock(2)`-based wrapper at `~/.cache/homelab/ansible-playbook.lock` is still deferred until parallel-agent ansible work becomes a recurring pattern. Semaphore's own single-replica StatefulSet serialises scheduled runs by accident — manual-from-MacBook concurrent with Semaphore is the gap.
- **PBS notification hook → Hermod `critical` on backup failure** — still deferred (was deferred during 5h.2 too).

## Lessons

1. **Silent success is worse than loud failure.** The NetBox cold-cache class is the canonical example: every layer reported "fine," and the fleet was completely unmonitored for one cycle. The fix is structural (host-count assertion), not behavioural (operator vigilance).
2. **Schema discovery is most of K8s app deploy.** Semaphore took ~12 commits of "this env var doesn't do what the docs say" before reaching a working config. The pattern across recent K8s app deploys (NetBox, Outline, Garage, Semaphore) is the same — pin a version, watch the pod fail, adjust the manifest. Allocating the time budget for schema-discovery up-front beats treating each new failure as a surprise.
3. **`--check` faithfulness is a per-role discipline, not a framework concern.** The drift-check wrapper can preflight-assert, retry NetBox, and split caches — but only the role can make a `uri:` POST `--check`-safe. The 20-commit shakeout is the bulk of the price for getting a clean baseline; future role additions need to factor this in at write time.
