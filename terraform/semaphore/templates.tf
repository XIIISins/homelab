# terraform/semaphore/templates.tf
#
# Templates per the design doc:
#
#   - refresh-netbox-inventory  cron */4h  ansible-side: ad-hoc command
#   - asgard-drift-check        cron */6h  --check --diff site.yml
#   - asgard-apply              manual     site.yml (full converge)
#   - asgard-nonprod-drift-check cron daily --check --diff site-nonprod.yml
#   - asgard-nonprod-apply      manual     site-nonprod.yml (canary pool;
#                                          notifications capped at alert)
#   - asgard-os-updates         manual     os-updates.yml (fleet OS patch
#                                          + reboot, serial per quorum
#                                          group)
#   - asgard-fleet-agents       cron daily fleet-agents.yml (defense-
#                                          in-depth vlagent + zabbix-
#                                          agent reconverge across
#                                          every host, complementing
#                                          the per-host-group agent
#                                          roles in site.yml)
#   - infra-health-check        cron */12h active prober (Cloudflare,
#                                          certs, Patroni, etcd)
#   - aiops-*                   manual     Phase 10c3 action registry
#                                          (aiops/actions.yml); see the
#                                          "AIOps action templates" block
#
# The drift-check + apply + fleet-agents templates run *wrapper*
# playbooks (drift-check.yml / apply.yml / fleet-agents.yml) rather
# than the underlying playbooks directly, so the hermod_summary
# callback (which infers mode from the first playbook's filename) can
# tell same-file reuse apart. os-updates.yml needs no wrapper — it's
# only ever run by this one template, so its own filename is a unique
# key in the callback's _MODES map.

# === refresh-netbox-inventory ===
#
# Wipes the netbox.netbox dynamic-inventory cache + actively rebuilds
# it via `ansible-inventory --list`, with retry-loop tolerance for
# transient NetBox 500s. cache_timeout (24h in netbox.yml) exceeds
# this template's refresh cadence (4h), so consumer playbooks always
# hit the warm cache between refreshes — the refresh template is the
# only NetBox-live path in steady state. See the script for details.
resource "semaphoreui_project_template" "refresh_netbox_inventory" {
  project_id     = semaphoreui_project.asgard.id
  name           = "refresh-netbox-inventory"
  description    = "Rebuild NetBox dynamic-inventory cache (with retry). Drift-check / apply read the cache, not live NetBox."
  app            = "bash"
  playbook       = "ansible/scripts/refresh-netbox-inventory.sh"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Failure → callback fires alert (NetBox down or cache path
  # unwritable). Success → silent (audit trail only).
  suppress_success_alerts = true
}

# === asgard-drift-check ===

resource "semaphoreui_project_template" "asgard_drift_check" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-drift-check"
  description    = "Read-only converge check across the fleet. Reports drift to Hermod."
  app            = "ansible"
  playbook       = "ansible/playbooks/drift-check.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # --check + --diff at the template level so it can't be forgotten.
  # arguments is a list-of-strings; Semaphore wraps each element as
  # a separate argv entry.
  arguments                   = ["--check", "--diff"]
  allow_override_args_in_task = false

  # Vault password supplied via the ansible_vault key — Semaphore
  # writes it to a temp file + passes --vault-password-file.
  # Provider schema (v0.2.2): `password` is a nested object containing
  # `vault_key_id` — NOT a `type = "password"` discriminator field with
  # `vault_key_id` at the top level (that's the WebFetch-documented
  # shape, but the actual schema differs). TF accepts the wrong shape
  # silently + stores vaults=null server-side, so playbook runs hit
  # "Attempting to decrypt but no vault secrets found" on any task
  # that loads an ansible-vault-encrypted var.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Success path can be noisy (clean drift-check every 6h) — only
  # surface failures + the callback-driven drift alerts.
  suppress_success_alerts = true
}

# === asgard-apply ===
#
# Manual-trigger template. No schedule; operator clicks "Run" after
# investigating a drift alert. Failure → Hermod `critical` (via
# callback when HERMOD_MODE=apply).
resource "semaphoreui_project_template" "asgard_apply" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-apply"
  description    = "Full converge across the fleet. Manual trigger after drift investigation."
  app            = "ansible"
  playbook       = "ansible/playbooks/apply.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Provider schema (v0.2.2): `password` is a nested object containing
  # `vault_key_id` — NOT a `type = "password"` discriminator field with
  # `vault_key_id` at the top level (that's the WebFetch-documented
  # shape, but the actual schema differs). TF accepts the wrong shape
  # silently + stores vaults=null server-side, so playbook runs hit
  # "Attempting to decrypt but no vault secrets found" on any task
  # that loads an ansible-vault-encrypted var.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Apply success IS news ("the converge worked, drift cleared") —
  # let Semaphore surface the success indicator + the callback
  # decides whether to actually POST to Hermod.
  suppress_success_alerts = false
}

# === asgard-os-updates ===
#
# Manual-trigger template. No schedule (operator decision 2026-09-17 —
# unattended fleet-wide reboots need more runway before going hands-off).
# Runs ansible/playbooks/os-updates.yml directly (no Semaphore wrapper
# needed — unlike site.yml, this playbook isn't reused by another
# template under a different mode, so no wrapper-file disambiguation is
# required; hermod_summary's _MODES matches "os-updates.yml" itself).
# Per-quorum-group serial:1 (see the playbook's own header) already
# avoids downtime; cordon/drain was evaluated and deliberately skipped —
# a package-upgrade reboot completes inside K3s's node-eviction grace
# period, so it doesn't evict pods, it just pauses that node's share
# while anti-affinity-spread replicas / Patroni / etcd keep serving.
resource "semaphoreui_project_template" "asgard_os_updates" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-os-updates"
  description    = "OS package patching across the fleet, serial per quorum group. Manual trigger."
  app            = "ansible"
  playbook       = "ansible/playbooks/os-updates.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Same vaults shape as the other ansible templates.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Patch success IS news, same reasoning as asgard-apply — the operator
  # wants confirmation the fleet patched + rebooted cleanly, not just
  # silence.
  suppress_success_alerts = false
}

# === asgard-fleet-agents ===
#
# Daily fleet-wide vlagent + zabbix-agent reconverge. Every per-host-
# group playbook in site.yml already ships these roles, so this is
# belt-and-braces — catches any host the per-group playbooks missed
# (future new playbook that forgets to add agents, etc.).
# Hermod posture matches apply: failure → critical, success → silent.
resource "semaphoreui_project_template" "asgard_fleet_agents" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-fleet-agents"
  description    = "Daily fleet-wide vlagent + zabbix-agent reconverge (defense-in-depth)."
  app            = "ansible"
  playbook       = "ansible/playbooks/fleet-agents.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # Same vaults shape as the other ansible templates.
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # Defense-in-depth job — successful daily runs are background noise,
  # only surface failures (which also fire Hermod `critical` via the
  # callback's apply-mode classification).
  suppress_success_alerts = true
}

# === asgard-nonprod-drift-check / asgard-nonprod-apply (Phase 10b1) ===
#
# The non-prod counterpart of drift-check/apply for the AIOps canary pool
# (site-nonprod.yml; the canaries are deliberately NOT in site.yml). They run
# the nonprod-* wrappers, which hermod_summary recognises by filename and CAPS
# at tag `info`: a failed canary posts "[non-prod] ... failed" to the FYI channel, never
# `critical`/Hrist, and changes-only drift is silent. See the callback header.
resource "semaphoreui_project_template" "asgard_nonprod_drift_check" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-nonprod-drift-check"
  description    = "Read-only converge check of non-prod hosts (canary pool). Notifications capped at info."
  app            = "ansible"
  playbook       = "ansible/playbooks/nonprod-drift-check.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  arguments                   = ["--check", "--diff"]
  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

resource "semaphoreui_project_template" "asgard_nonprod_apply" {
  project_id     = semaphoreui_project.asgard.id
  name           = "asgard-nonprod-apply"
  description    = "Full converge of non-prod hosts (canary pool). Manual trigger; failure posts info, never alert/critical."
  app            = "ansible"
  playbook       = "ansible/playbooks/nonprod-apply.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

# === infra-health-check (Wave S4) ===
#
# Active prober (hosts: localhost in the Semaphore pod): Cloudflare token
# validity, served TLS cert expiry per zone, Patroni cluster health, etcd
# quorum. POSTs its own findings to Hermod (critical/alert tags) — the
# hermod_summary callback no-ops for non-drift/apply wrappers, so the
# only Hermod traffic is the playbook's explicit per-finding POSTs.
# A clean run is silent.
resource "semaphoreui_project_template" "infra_health_check" {
  project_id     = semaphoreui_project.asgard.id
  name           = "infra-health-check"
  description    = "Active prober: CF token, cert expiry, Patroni + etcd quorum. Alerts to Hermod on finding."
  app            = "ansible"
  playbook       = "ansible/playbooks/infra-health-check.yml"
  repository_id  = semaphoreui_project_repository.homelab.id
  inventory_id   = semaphoreui_project_inventory.netbox.id
  environment_id = semaphoreui_project_environment.default.id

  # No ansible-vault var is loaded by this playbook, but include the same
  # vaults shape as the other ansible templates so an inventory parse that
  # touches group_vars/all/vault.yml never trips "no vault secrets found".
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.ansible_vault.id
      }
    },
  ]

  # The playbook POSTs its own findings to Hermod; Semaphore's own
  # success/failure alert is redundant noise on a clean run.
  suppress_success_alerts = true
}

# === AIOps action templates (Phase 10c3) ===
#
# One template per entry in aiops/actions.yml (the action registry); that file
# is the source of truth for tier, typed extra-vars, guard and verify step, and
# `python3 aiops/tools/lint.py` checks that every registry template name and
# playbook below matches. Manual-trigger only: no schedules. The 10e executor
# will trigger ONLY these (allow-listed-template key); extra-vars arrive as the
# task `environment` (JSON), which Semaphore honours even with
# allow_override_args_in_task = false.
#
# These templates live in the dedicated `aiops` project (main.tf), NOT in `asgard`: the executor's Semaphore user is
# Task Runner on that project only, so its token cannot start asgard-apply or any other fleet template.
# Moving them recreates them (no schedules, no history that matters); the registry resolves templates by NAME.
#
# No wrapper-file concern: the new playbook filenames are absent from
# hermod_summary's _MODES map, so they generate no Hermod traffic of their own.

# --- T0: read-only ---

resource "semaphoreui_project_template" "aiops_service_status" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-service-status"
  description    = "AIOps T0: read one systemd unit's state on one host (extra-vars target_host, unit)."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-service-status.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  # Same vaults shape as the other ansible templates (inventory parse touches
  # group_vars/all/vault.yml).
  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

resource "semaphoreui_project_template" "aiops_vault_status" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-vault-status"
  description    = "AIOps T0: unauthenticated Vault seal/HA status via the vault CLI on Frigg."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-vault-status.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

resource "semaphoreui_project_template" "aiops_patroni_status" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-patroni-status"
  description    = "AIOps T0: Patroni cluster state from the open GET /cluster endpoint on the PG trio."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-patroni-status.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

# replay-role-check and replay-role are the two templates that need CLI args
# per run (--limit is a task field; --tags must come as task `arguments`), so
# they are the only ones with allow_override_args_in_task = true. The authority
# is NOT this flag but the in-playbook guard (aiops-replay-guard.yml): it fails
# the run unless the limit is one T1 host, the tag is allow-listed and the
# check-mode matches. check variant bakes --check --diff as the default args.
resource "semaphoreui_project_template" "aiops_replay_role_check" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-replay-role-check"
  description    = "AIOps T0: --check --diff of site.yml for ONE T1 host and ONE allow-listed role tag. Always precedes aiops-replay-role."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-replay-role-check.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  arguments                   = ["--check", "--diff"]
  allow_override_args_in_task = true

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

# --- T1: mutating, approval-gated until the 10f1 guards exist ---

resource "semaphoreui_project_template" "aiops_restart_unit" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-restart-unit"
  description    = "AIOps T1: restart one allow-listed systemd unit on one T1 host and wait for active."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-restart-unit.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = false
}

resource "semaphoreui_project_template" "aiops_replay_role" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-replay-role"
  description    = "AIOps T1: converge ONE allow-listed role tag on ONE T1 host via site.yml --limit/--tags. Requires a clean aiops-replay-role-check first."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-replay-role.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  # No baked args: the executor supplies --tags; the guard rejects --check.
  allow_override_args_in_task = true

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = false
}

# Shared by the registry actions flux-reconcile (reset=false) and
# flux-reconcile-reset (reset=true); the executor sets the fixed var.
# Runs on Frigg (flux CLI + operator kubeconfig live there).
resource "semaphoreui_project_template" "aiops_flux_reconcile" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-flux-reconcile"
  description    = "AIOps T1: flux reconcile hr <hr_name> -n <hr_namespace> [--reset] on Frigg; stateful-release deny-list + reset allow-list enforced in the playbook."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-flux-reconcile.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = false
}

# --- Phase 10g rebuild loop (canary class first; engine wiring is a separate slice, the registry actions stay
# applied: false / planned: true until this block is applied and the engine can drive them) ---
#
# start-guest runs against the PVE host (pct start, canary VMIDs 1190-1192 only); rebuild-converge needs a single-host
# `limit` task field equal to `target` (the executor sets it); rebuild-verify is read-only. All three re-check the
# registry (aiops/actions.yml) inside the playbook, so the template is not the authority.

resource "semaphoreui_project_template" "aiops_start_guest" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-start-guest"
  description    = "AIOps T1 (10g rung 0): start ONE stopped canary LXC (VMID 1190-1192) through its PVE host with pct; never creates or destroys."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-start-guest.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = false
}

resource "semaphoreui_project_template" "aiops_rebuild_converge" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-rebuild-converge"
  description    = "AIOps T1 (10g step 6): Day-1 baseline as root (only if root still answers) then the canary full play as ansible, after Terraform recreated the guest. Needs --limit equal to target."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-rebuild-converge.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  # true: the executor sets the task `limit` (registry task_fields limit: '{target}'); without it Semaphore ignores the limit
  # and the play's guard runs against the first inventory host (found live 2026-10-03). The playbook's own guard asserts
  # ansible_limit == target, so a wrong or missing limit is refused there.
  allow_override_args_in_task = true

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = false
}

resource "semaphoreui_project_template" "aiops_rebuild_verify" {
  project_id     = semaphoreui_project.aiops.id
  name           = "aiops-rebuild-verify"
  description    = "AIOps T0 (10g step 7): read-only canary post-conditions (ssh as ansible, vlagent + zabbix-agent2 active, Zabbix group and template, alert cleared)."
  app            = "ansible"
  playbook       = "ansible/playbooks/aiops-rebuild-verify.yml"
  repository_id  = semaphoreui_project_repository.aiops_homelab.id
  inventory_id   = semaphoreui_project_inventory.aiops_netbox.id
  environment_id = semaphoreui_project_environment.aiops_default.id

  allow_override_args_in_task = false

  vaults = [
    {
      name = "default"
      password = {
        vault_key_id = semaphoreui_project_key.aiops_ansible_vault.id
      }
    },
  ]

  suppress_success_alerts = true
}

# === Schedules ===

resource "semaphoreui_project_schedule" "refresh_netbox_inventory" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.refresh_netbox_inventory.id
  name        = "every-4h"
  cron_format = "0 */4 * * *"
  enabled     = true
}

resource "semaphoreui_project_schedule" "asgard_drift_check" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.asgard_drift_check.id
  name        = "every-6h"
  # Offset 15 min past the hour so it doesn't collide with the
  # inventory-refresh cron at minute 0 (drift-check reads the
  # cache the refresh just wrote — give it 15 min to settle).
  cron_format = "15 */6 * * *"
  enabled     = true
}

# asgard-apply has no schedule — manual-only.

resource "semaphoreui_project_schedule" "asgard_nonprod_drift_check" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.asgard_nonprod_drift_check.id
  name        = "daily"
  # 05:15 UTC daily: low frequency (canaries carry nothing), after the
  # inventory-refresh at minute 0 of 04:00 and the 04:30 fleet-agents sweep.
  cron_format = "15 5 * * *"
  enabled     = true
}

# asgard-nonprod-apply has no schedule — manual-only.

resource "semaphoreui_project_schedule" "asgard_fleet_agents" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.asgard_fleet_agents.id
  name        = "daily"
  # 04:30 UTC daily — quiet hours for the homelab, after the
  # inventory-refresh cron at minute 0 + drift-check at minute 15.
  # Use minute 30 to keep crons visually grouped on the hour.
  cron_format = "30 4 * * *"
  enabled     = true
}

resource "semaphoreui_project_schedule" "infra_health_check" {
  project_id  = semaphoreui_project.asgard.id
  template_id = semaphoreui_project_template.infra_health_check.id
  name        = "every-12h"
  # 06:45 + 18:45 UTC — twice daily, well clear of the minute-0/15/30
  # cron cluster. Cert expiry + token validity don't need finer than 12h
  # (cert_warn_days=14 gives ~28 chances to alert before expiry).
  cron_format = "45 6,18 * * *"
  enabled     = true
}
